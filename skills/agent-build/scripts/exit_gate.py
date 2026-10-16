#!/usr/bin/env python3
"""Agent Build: the exit gate (criterion 15; pins P2b, P3, P6, P8, G9, G10, H1, H7, I5, I9, I13, I14).

Reads every counted record of a run folder, re-derives the comparison,
coverage and core verdicts from the raw run-this-case outputs (never trusting
a stored verdict), applies the P8 applicability table and requires one equal
package hash. Writes R/records/exit.json and appends exit.written.

Usage:
  exit_gate.py --run R

Exit: 0 done (verdict pass); 1 not done (verdict fail); 2 usage error (writes nothing).
"""
import argparse
import importlib.util
import json
import os
import re
import sys

# The skill's numbers.py (U9) shares its name with the stdlib module that
# decimal imports. Load decimal (and so the stdlib numbers) with only the
# stdlib folders on sys.path, before any skill folder can shadow it.
_STDLIB = os.path.dirname(os.__file__)
_saved_path = sys.path[:]
sys.path[:] = [p for p in sys.path if p == _STDLIB or not os.path.isfile(os.path.join(p or ".", "numbers.py"))]
sys.modules.pop("numbers", None)
import decimal  # noqa: E402,F401
sys.path[:] = _saved_path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import _records  # noqa: E402
import log  # noqa: E402

NMI = "needs_more_information"

CHECK_NAMES = (
    "freeze-verify", "e2e-early", "e2e-final", "suite", "stoprules", "numbers", "core",
    "live", "live-model-calls", "cold", "clearance", "open-items", "residuals", "handover",
    "package-hash",
)

# Checks whose cases can belong to one process (criterion 19); every other
# check counts for every process.
CASE_SCOPED = ("e2e-final", "suite", "live", "live-model-calls")

PASSING = ("pass", "not-applicable", "exempted")

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.S)


class Check(object):
    def __init__(self, name):
        self.name = name
        self.applies = True
        self.reason = ""
        self.result = "pass"
        self.reasons = []
        self.per_process = {}  # pid -> [reason] for case-scoped failures
        self.unscoped = False  # a failure that counts against every process

    def fail(self, result, reason, scoped=False):
        """Record a problem; the first non-pass result stays."""
        if self.result in PASSING:
            self.result = result
        if not scoped:
            self.unscoped = True
        self.reasons.append("%s: %s" % (self.name, reason))

    def to_json(self):
        reason = self.reason or "; ".join(self.reasons)
        return {"name": self.name, "applies": self.applies, "reason": reason, "result": self.result}


_LOADED = {}


def _import(name):
    """Load the skill's module <name>.py by file path from this scripts folder (pins G10).

    `numbers` is also a standard-library module (decimal imports it), so the
    skill's modules are never imported by their bare names and sys.path is
    never searched: only SCRIPTS_DIR/<name>.py is loaded, as `ab_<name>`, and
    an absent file is `module not present` (never the stdlib module).
    """
    if name in _LOADED:
        return _LOADED[name]
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    if not os.path.isfile(path):
        result = (None, "could not be re-derived: module not present (%s)" % name)
    else:
        try:
            spec = importlib.util.spec_from_file_location("ab_" + name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["ab_" + name] = mod
            spec.loader.exec_module(mod)
            result = (mod, None)
        except Exception as err:  # a module that fails on import cannot re-derive either
            result = (None, "could not be re-derived: %s failed to import (%s)" % (name, err))
    _LOADED[name] = result
    return result


def _sha(path):
    return _records.sha256_file(path) if os.path.isfile(path) else None


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class Gate(object):
    def __init__(self, run):
        self.run = run
        self.checks = dict((n, Check(n)) for n in CHECK_NAMES)
        self.run_reasons = []
        self.brief, err = _records.load_json(self.p("brief.json"))
        if not isinstance(self.brief, dict):
            self.run_reasons.append("brief.json %s" % (err or "is not a JSON object"))
            self.brief = {}
        try:
            self.events = log.read(run)
        except (ValueError, OSError, UnicodeDecodeError) as err:
            self.run_reasons.append("run log unreadable: %s" % err)
            self.events = []
        self.package_sha = None
        sha_path = self.p("package.sha256")
        if os.path.isfile(sha_path):
            with open(sha_path, encoding="utf-8") as f:
                self.package_sha = f.readline().strip() or None
        self.manifest, err = _records.load_json(self.p("frozen/MANIFEST.json"))
        self.version = None
        self.version_entry = None
        if isinstance(self.manifest, dict):
            self.version = self.manifest.get("current_version")
            self.version_entry = self.version_of(self.version)
        if self.version_entry is None:
            self.run_reasons.append("frozen/MANIFEST.json has no readable current version")
        self.processes = [p for p in (self.brief.get("processes") or []) if isinstance(p, str)]
        if not self.processes:
            self.run_reasons.append("brief.json processes is missing or empty")
        self.current_inputs = {}
        for name in _records.STANDARD_INPUTS:
            self.current_inputs[name] = _sha(self.p(name))
        self.dispositions, self.disposition_problems = self.load_dispositions()

    # ---- helpers -------------------------------------------------------
    def p(self, rel):
        return os.path.join(self.run, rel)

    def version_of(self, version):
        if not isinstance(self.manifest, dict):
            return None
        for v in self.manifest.get("versions") or []:
            if isinstance(v, dict) and v.get("version") == version and _is_int(version):
                return v
        return None

    def label_suite(self):
        return "suite-%s" % (self.package_sha or "")[:12]

    def capabilities(self, pid=None):
        caps = {}
        doc, _ = _records.load_json(self.p("capabilities.json"))
        if isinstance(doc, dict):
            caps.update(doc.get("capabilities") or {})
            if pid is not None:
                caps.update((doc.get("per_process") or {}).get(pid) or {})
        override = self.brief.get("capabilities")
        if isinstance(override, dict):
            caps.update(dict((k, v) for k, v in override.items() if k != "per_process"))
            if pid is not None:
                caps.update((override.get("per_process") or {}).get(pid) or {})
        return caps

    def load_dispositions(self):
        path = self.p("records/dispositions.jsonl")
        if not os.path.isfile(path):
            return [], []
        items, problems = [], []
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    problems.append("records/dispositions.jsonl line %d is not valid JSON" % i)
                    continue
                if isinstance(obj, dict):
                    items.append(obj)
                else:
                    problems.append("records/dispositions.jsonl line %d is not a JSON object" % i)
        return items, problems

    def last_written(self, rel):
        sha = None
        for e in self.events:
            if e.get("type") == "record.written" and (e.get("payload") or {}).get("path") == rel:
                sha = e["payload"].get("sha256")
        return sha

    def counted(self, check, rel, early=False, reads_frozen=False, package_bound=True):
        """Load a counted record and check integrity and freshness (pins H1, H7, P3).

        Returns the record, or None when it cannot be counted (the check is marked).
        """
        path = self.p(rel)
        if not os.path.isfile(path):
            check.fail("missing", "%s missing" % rel)
            return None
        rec, err = _records.load_json(path)
        if not isinstance(rec, dict):
            check.fail("unknown", "%s unreadable (%s)" % (rel, err or "not a JSON object"))
            return None
        logged = self.last_written(rel)
        if logged is None:
            check.fail("stale", "%s: record not logged" % rel)
        elif logged != _records.sha256_file(path):
            check.fail("stale", "%s: record changed after writing" % rel)
        hashes = rec.get("input_hashes")
        if not isinstance(hashes, dict):
            hashes = {}
        for name in _records.STANDARD_INPUTS:
            if early and name == "frozen/MANIFEST.json":
                continue
            cur = self.current_inputs.get(name)
            if cur is None:
                check.fail("stale", "%s: %s is missing from the run folder" % (rel, name))
            elif name not in hashes:
                check.fail("stale", "%s: input hash for %s missing" % (rel, name))
            elif hashes[name] != cur:
                check.fail("stale", "%s: %s changed since the record was written" % (rel, name))
        if not early:
            # Only the P3 package-bound records carry code_identity.package_sha256;
            # freeze-verify is judged on its verdict, version and H1 hash alone.
            if package_bound:
                ident = rec.get("code_identity") if isinstance(rec.get("code_identity"), dict) else {}
                stored = ident.get("package_sha256")
                if self.package_sha is None:
                    check.fail("missing", "%s: package.sha256 missing, record cannot be bound" % rel)
                elif stored != self.package_sha:
                    check.fail("stale", "%s: package_sha256 %s differs from package.sha256 %s"
                               % (rel, stored, self.package_sha))
            if reads_frozen or "manifest_version" in rec:
                mv = rec.get("manifest_version")
                if mv != self.version:
                    check.fail("stale", "%s: manifest version %s is not the current version %s"
                               % (rel, mv, self.version))
        return rec

    def stored_verdict_vs(self, check, rel, stored, derived):
        if stored != derived:
            check.reasons.append("%s: stored verdict %s differs from re-derived %s (re-derived decides)"
                                 % (rel, stored, derived))
            if derived != "pass" and check.result in PASSING:
                check.result = "fail"

    # ---- frozen cases --------------------------------------------------
    def frozen_cases(self):
        """Return [{case_id, process, case, expected, case_entry, expected_entry, expected_sha256}]."""
        out = []
        if self.version_entry is None:
            return out
        files = [f for f in self.version_entry.get("files") or [] if isinstance(f, dict)]
        expected = dict((f.get("case_id"), f) for f in files if f.get("kind") == "expected")
        for f in files:
            if f.get("kind") != "case":
                continue
            cid = f.get("case_id")
            case_doc, _ = _records.load_json(self.p(os.path.join("frozen", f.get("path") or "")))
            exp_entry = expected.get(cid)
            exp_doc, exp_sha = None, None
            if exp_entry is not None:
                exp_path = self.p(os.path.join("frozen", exp_entry.get("path") or ""))
                exp_doc, _ = _records.load_json(exp_path)
                exp_sha = _sha(exp_path)
            process = f.get("process")
            if process is None and isinstance(case_doc, dict):
                process = case_doc.get("process")
            out.append({"case_id": cid, "process": process,
                        "case": case_doc if isinstance(case_doc, dict) else None,
                        "expected": exp_doc if isinstance(exp_doc, dict) else None,
                        "case_entry": f, "expected_entry": exp_entry, "expected_sha256": exp_sha})
        return out

    def tolerance(self, fc):
        exp = fc["expected"] or {}
        if exp.get("tolerance") is not None:
            return exp["tolerance"]
        if self.brief.get("amount_tolerance") is not None:
            return self.brief["amount_tolerance"]
        if (fc["case"] or {}).get("source") == "agent-design-suite":
            return 0.01
        return None

    @staticmethod
    def manifest_obj(fc):
        def part(entry):
            entry = entry or {}
            return {"state": entry.get("state", NMI) if entry else "pending",
                    "pending_reason": entry.get("pending_reason") if entry else "no manifest entry"}
        return {"case": part(fc["case_entry"]), "expected": part(fc["expected_entry"])}

    def compare_one(self, compare, fc, label):
        out_path = self.p("outputs/%s/%s.json" % (label, fc["case_id"]))
        output, _ = _records.load_json(out_path)
        if not isinstance(output, dict):
            output = None
        res = compare.compare_case(fc["case"], fc["expected"], output, self.tolerance(fc),
                                   manifest=self.manifest_obj(fc), run=self.run)
        return res, output

    # ---- checks --------------------------------------------------------
    def check_freeze(self):
        c = self.checks["freeze-verify"]
        freeze, err = _import("freeze")
        if freeze is None:
            c.fail("unknown", err)
        else:
            try:
                ok, problems = freeze.verify(self.run)
            except Exception as e:
                ok, problems = False, ["freeze.verify raised %s" % e]
            if not ok:
                c.fail("fail", "freeze.verify reported: %s" % "; ".join(str(p) for p in problems))
        rec = self.counted(c, "records/freeze-verify.json", reads_frozen=True, package_bound=False)
        if rec is not None and rec.get("problems"):
            c.fail("fail", "records/freeze-verify.json lists problems")
        if rec is not None and rec.get("verdict") != "pass":
            c.fail("fail", "records/freeze-verify.json verdict %s" % rec.get("verdict"))

    def round_two_start(self):
        for e in self.events:
            pl = e.get("payload") or {}
            if e.get("type") == "round.started" and pl.get("round") == 2:
                return pl.get("first_launch") or e.get("ts")
        return None

    def check_early(self):
        c = self.checks["e2e-early"]
        rel = "records/e2e-early.json"
        rec = self.counted(c, rel, early=True)
        # Round boundary evidence (pin I9).
        split_rel = self.brief.get("split_path")
        units = None
        if not isinstance(split_rel, str) or not split_rel:
            c.fail("unknown", "brief.json split_path missing")
        else:
            path = self.p(split_rel)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError) as e:
                text = None
                c.fail("unknown", "split file %s missing or unreadable (%s)" % (split_rel, e))
            if text is not None:
                blocks = _JSON_BLOCK.findall(text)
                try:
                    doc = json.loads(blocks[0]) if len(blocks) == 1 else None
                except ValueError:
                    doc = None
                if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
                    c.fail("unknown", "split file %s has no readable G23 block" % split_rel)
                else:
                    units = doc["units"]
        round2 = self.round_two_start()
        if units is not None and round2 is None:
            if any(isinstance(u, dict) and _is_int(u.get("round")) and u["round"] >= 2 for u in units):
                c.fail("unknown", "no round 2 boundary evidence")
        if rec is None:
            return
        ident = rec.get("code_identity") if isinstance(rec.get("code_identity"), dict) else {}
        commit = ident.get("commit")
        if not isinstance(commit, str) or not commit or commit == NMI:
            c.fail("fail", "early record has no commit (%s)" % commit)
        mv = rec.get("manifest_version")
        ventry = self.version_of(mv)
        if ventry is None:
            c.fail("fail", "early record manifest version %s is not in frozen/MANIFEST.json" % mv)
        else:
            by_path = dict(("frozen/" + f.get("path", ""), f.get("sha256"))
                           for f in ventry.get("files") or [] if isinstance(f, dict))
            frozen_hashes = dict((k, v) for k, v in (rec.get("input_hashes") or {}).items()
                                 if k.startswith("frozen/") and k != "frozen/MANIFEST.json")
            if not frozen_hashes:
                c.fail("fail", "early record carries no frozen-file hashes")
            for k, v in sorted(frozen_hashes.items()):
                if by_path.get(k) != v:
                    c.fail("fail", "early record hash for %s differs from version %s" % (k, mv))
        if rec.get("verdict") != "pass":
            c.fail("fail", "early record verdict %s" % rec.get("verdict"))
        comp = rec.get("comparison") if isinstance(rec.get("comparison"), dict) else {}
        if comp.get("state") != "pass":
            c.fail("fail", "early record comparison %s" % comp.get("state"))
        for probe in rec.get("probes") or []:
            if isinstance(probe, dict) and probe.get("applies") is True and probe.get("result") != "pass":
                c.fail("fail", "early record probe %s %s" % (probe.get("name"), probe.get("result")))
        produced = rec.get("produced_at")
        if round2 is not None:
            if not isinstance(produced, str) or produced > round2:
                c.fail("fail", "early record produced_at %s is later than the round 2 launch %s"
                       % (produced, round2))

    def check_final(self, compare, cerr):
        c = self.checks["e2e-final"]
        rel = "records/e2e-final.json"
        rec = self.counted(c, rel, reads_frozen=True)
        if rec is None:
            return
        if compare is None:
            c.fail("unknown", cerr)
            return
        cid = rec.get("case_id")
        fcs = [fc for fc in self.frozen_cases() if fc["case_id"] == cid]
        if not fcs:
            c.fail("fail", "final record case %s is not in the current manifest" % cid)
            return
        res, _ = self.compare_one(compare, fcs[0], "final")
        derived = res.get("state")
        stored = (rec.get("comparison") or {}).get("state") if isinstance(rec.get("comparison"), dict) else None
        if stored != derived:
            c.reasons.append("e2e-final: stored comparison %s differs from re-derived %s (re-derived decides)"
                             % (stored, derived))
        if derived != "pass":
            msg = "final output for %s re-derived %s (%s)" % (cid, derived, res.get("reason"))
            c.fail("fail", msg, scoped=True)
            for pid in self.processes:
                if fcs[0]["process"] in (None, pid):
                    c.per_process.setdefault(pid, []).append("e2e-final: " + msg)
        if rec.get("verdict") != "pass" and derived == "pass":
            c.fail("fail", "final record verdict %s" % rec.get("verdict"))

    def rederive_cases(self, check, compare, rec, rel, label, live=False):
        """Re-derive every case of the current version for a suite-like record; returns case dicts."""
        stored = dict((cs.get("case_id"), cs) for cs in rec.get("cases") or [] if isinstance(cs, dict))
        cases = []
        artifact_sha = {}
        for fc in self.frozen_cases():
            res, output = self.compare_one(compare, fc, label)
            state = res.get("state")
            if live and state == "fail":
                state = "pending"  # pin G37: a live disagreement waits for a person's disposition
            arts = {}
            for name, path in ((output or {}).get("artifacts") or {}).items():
                if isinstance(path, str):  # keyed by run-relative path, as the disposition writer does (E2)
                    arts[compare.artifact_key(self.run, path)] = _sha(self.p(path))
            artifact_sha[fc["case_id"]] = arts
            case = dict(stored.get(fc["case_id"]) or {"case_id": fc["case_id"]})
            case.update({"state": state, "reason": res.get("reason"),
                         "critical": (fc["expected"] or {}).get("critical"),
                         "judgement_needed": (fc["expected"] or {}).get("judgement_needed"),
                         "exclusion": (fc["expected"] or {}).get("exclusion"),
                         "expected_sha256": fc["expected_sha256"],
                         "manifest": self.manifest_obj(fc)})
            cases.append((fc, case))
        current = {"package_sha256": self.package_sha,
                   "expected_sha256": dict((fc["case_id"], fc["expected_sha256"]) for fc, _ in cases),
                   "artifact_sha256": artifact_sha}
        applied, stale = compare.apply_dispositions([cs for _, cs in cases], self.dispositions, current)
        for s in stale or []:
            check.reasons.append("%s: stale disposition ignored: %s" % (check.name, s))
        for p in self.disposition_problems:
            check.fail("unknown", p)
        result = []
        for (fc, _), case in zip(cases, applied):
            sc = stored.get(fc["case_id"])
            if sc is None:
                check.fail("fail", "%s has no entry for case %s" % (rel, fc["case_id"]))
            elif sc.get("state") != case.get("state"):
                check.reasons.append("%s: %s stored %s, re-derived %s (re-derived decides)"
                                     % (rel, fc["case_id"], sc.get("state"), case.get("state")))
            result.append((fc, case))
        return result

    def aggregate_check(self, check, compare, rec, rel, pairs):
        agg = compare.aggregate([cs for _, cs in pairs], self.brief)
        derived = agg.get("state")
        stored = (rec.get("aggregate") or {}).get("state") if isinstance(rec.get("aggregate"), dict) else None
        if stored != derived:
            check.reasons.append("%s: stored aggregate %s differs from re-derived %s (re-derived decides)"
                                 % (rel, stored, derived))
        derived_verdict = "pass" if derived == "pass" else "fail"
        if rec.get("verdict") != derived_verdict:
            check.reasons.append("%s: stored verdict %s differs from re-derived %s (re-derived decides)"
                                 % (rel, rec.get("verdict"), derived_verdict))
        if derived != "pass":
            failing = ["%s %s" % (cs["case_id"], cs.get("state")) for _, cs in pairs if cs.get("state") != "pass"]
            check.fail("fail", "aggregate %s (%s)" % (derived, ", ".join(failing) or "; ".join(agg.get("reasons") or [])),
                       scoped=True)
        for pid in self.processes:
            mine = [cs for fc, cs in pairs if fc["process"] in (None, pid)]
            pagg = compare.aggregate(mine, self.brief)
            if pagg.get("state") != "pass":
                bad = ["%s %s" % (cs["case_id"], cs.get("state")) for cs in mine if cs.get("state") != "pass"]
                check.per_process.setdefault(pid, []).append(
                    "%s: aggregate %s for process %s (%s)" % (check.name, pagg.get("state"), pid, ", ".join(bad)))

    def check_suite(self, compare, cerr):
        c = self.checks["suite"]
        rel = "records/%s.json" % self.label_suite()
        if self.package_sha is None:
            c.fail("missing", "package.sha256 missing, suite record cannot be named")
            return
        rec = self.counted(c, rel, reads_frozen=True)
        if rec is None:
            return
        if compare is None:
            c.fail("unknown", cerr)
            return
        pairs = self.rederive_cases(c, compare, rec, rel, self.label_suite())
        self.aggregate_check(c, compare, rec, rel, pairs)

    def check_live(self, compare, cerr):
        c = self.checks["live"]
        rel = "records/live.json"
        rec = self.counted(c, rel, reads_frozen=True)
        if rec is None:
            return
        usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
        uses = [self.capabilities(pid).get("uses_model", NMI) for pid in self.processes]
        if uses and all(u is False for u in uses) and rec.get("usage") is None:
            pass  # no model is used, so there is no model host usage report to require (criterion 13)
        elif usage.get("source") == "attested":
            ev = usage.get("evidence")
            if not usage.get("attestor") or not isinstance(ev, str) or not ev \
                    or not os.path.isfile(self.p(ev)) or os.path.getsize(self.p(ev)) == 0:
                c.fail("missing", "usage attestation without attestor or evidence file")
        elif usage.get("source") != "observed":
            c.fail("missing", "usage source %s is neither observed nor attested" % usage.get("source"))
        if compare is None:
            c.fail("unknown", cerr)
            return
        pairs = self.rederive_cases(c, compare, rec, rel, "live", live=True)
        self.aggregate_check(c, compare, rec, rel, pairs)

    def check_model_calls(self):
        c = self.checks["live-model-calls"]
        per = {}
        for pid in self.processes:
            per[pid] = self.capabilities(pid).get("uses_model", NMI)
        if all(v is False for v in per.values()) and per:
            c.applies = False
            c.result = "not-applicable"
            c.reason = "uses_model is false"
            return
        if any(v == NMI or v not in (True, False) for v in per.values()) or not per:
            c.applies = NMI
            c.result = "unknown"
            c.reason = "uses_model is needs_more_information"
            c.reasons.append("live-model-calls: uses_model is needs_more_information")
            return
        live_dir = self.p("outputs/live")
        frozen = dict((fc["case_id"], fc["process"]) for fc in self.frozen_cases())
        for pid, uses in sorted(per.items()):
            if uses is not True:
                continue
            total = 0
            for cid, proc in frozen.items():
                if proc not in (None, pid):
                    continue
                out, _ = _records.load_json(os.path.join(live_dir, cid + ".json"))
                calls = (out or {}).get("model_calls") if isinstance(out, dict) else None
                if _is_int(calls):
                    total += calls
            if total <= 0:
                msg = "model calls 0 in the live outputs for process %s while uses_model is true" % pid
                c.fail("fail", msg, scoped=True)
                c.per_process.setdefault(pid, []).append("live-model-calls: " + msg)

    def check_module(self, name, compare_label=True):
        c = self.checks[name]
        rel = "records/%s.json" % name
        rec = self.counted(c, rel)
        mod, err = _import(name)
        if mod is None:
            c.fail("unknown", err)
            return
        # Core (criterion 12) and numbers (criterion 11) check the suite and the live outputs; stoprules the suite only.
        both = name in ("core", "numbers")
        labels = [self.label_suite(), "live"] if both else [self.label_suite()]
        derived_by_label = {}
        for label in labels:
            try:
                if name == "core":
                    derived_rec = mod.evaluate(self.run, label, self.p("package"))
                else:
                    derived_rec = mod.evaluate(self.run, label)
            except Exception as e:
                c.fail("unknown", "could not be re-derived on %s: %s.evaluate raised %s" % (label, name, e))
                return
            derived = (derived_rec or {}).get("verdict") if isinstance(derived_rec, dict) else None
            derived_by_label[label] = derived
            if derived != "pass":
                why = "; ".join((derived_rec or {}).get("reasons") or []) if isinstance(derived_rec, dict) else ""
                where = (" on %s" % label) if both else ""
                c.fail("fail", "re-derived verdict%s %s%s" % (where, derived, (" (%s)" % why) if why else ""))
        if rec is not None:
            stored_label = rec.get("label") if rec.get("label") in derived_by_label else labels[0]
            self.stored_verdict_vs(c, rel, rec.get("verdict"), derived_by_label[stored_label])

    def check_stoprules(self):
        c = self.checks["stoprules"]
        rules = self.brief.get("rules")
        if not isinstance(rules, list):
            c.applies = NMI
            c.result = "unknown"
            c.reason = "brief.json rules missing"
            return
        decides = [r.get("stops_work") if isinstance(r, dict) else NMI for r in rules]
        stops = any(d is True for d in decides)
        if not stops and any(d is not False for d in decides):
            # Only a confirmed false makes the check not apply; missing is never false.
            c.applies = NMI
            c.result = "unknown"
            c.reason = "a rule's stops_work is needs_more_information"
            c.reasons.append("stoprules: a rule's stops_work is needs_more_information")
            return
        if not stops:
            c.applies = False
            c.result = "not-applicable"
            c.reason = "no rule has stops_work: true"
            return
        self.check_module("stoprules")

    def check_cold(self):
        c = self.checks["cold"]
        rec = self.counted(c, "records/cold.json")
        if rec is None:
            return
        ev = rec.get("evidence")
        if not rec.get("attestor") or not isinstance(ev, str) or not ev \
                or not os.path.isfile(self.p(ev)) or os.path.getsize(self.p(ev)) == 0:
            c.fail("missing", "cold record without attestor or evidence file")
        if rec.get("install") != "pass":
            c.fail("fail", "install %s" % rec.get("install"))
        if rec.get("empty_folder") is not True:
            c.fail("fail", "not run in an empty folder")
        if rec.get("reused_state") is not False:
            c.fail("fail", "reused state")
        if rec.get("files_missing"):
            c.fail("fail", "files missing: %s" % ", ".join(map(str, rec["files_missing"])))
        for p in rec.get("probes") or []:
            if isinstance(p, dict) and p.get("applies") is True and p.get("result") != "pass":
                c.fail("fail", "probe %s %s" % (p.get("name"), p.get("result")))
        for cs in rec.get("cases") or []:
            if not isinstance(cs, dict) or cs.get("state") != "pass":
                c.fail("fail", "case %s %s" % ((cs or {}).get("case_id"), (cs or {}).get("state")))
        if rec.get("verdict") != "pass":
            c.fail("fail", "verdict %s" % rec.get("verdict"))

    def check_clearance(self):
        c = self.checks["clearance"]
        rec = self.counted(c, "records/clearance.json")
        if rec is None:
            return
        for flag in rec.get("flags") or []:
            if not isinstance(flag, dict) or not flag.get("disposition"):
                c.fail("fail", "undisposed flag %s" % (flag or {}).get("id"))
        # Image-scan applicability per process (pins P8, G6): any process that
        # ships as a Docker image needs the scan, whatever the run-level value.
        scan = rec.get("image_scan")
        docker = dict((pid, self.capabilities(pid if self.processes else None).get("ships_as_docker_image", NMI))
                      for pid in self.processes or ["(run)"])
        shipping = sorted(pid for pid, v in docker.items() if v is True)
        undecided = sorted(pid for pid, v in docker.items() if v not in (True, False))
        if shipping and scan != "pass":
            c.fail("fail", "image scan %s while process %s ships as a Docker image"
                   % (scan, ", ".join(shipping)))
        elif undecided:
            c.fail("unknown", "ships_as_docker_image is needs_more_information for process %s"
                   % ", ".join(undecided))
        if rec.get("verdict") != "pass":
            c.fail("fail", "verdict %s" % rec.get("verdict"))

    def check_open_items(self):
        c = self.checks["open-items"]
        doc, err = _records.load_json(self.p("open-items.json"))
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
            c.fail("missing", "open-items.json %s" % (err or "has no items list"))
            return
        open_items = [i for i in doc["items"] if isinstance(i, dict) and i.get("status") == "open"]
        if not open_items:
            return
        required = set()
        by_id = {}
        for item in open_items:
            by_id[item.get("id")] = item
            for cid in item.get("test_case_ids") or []:
                required.add((item.get("id"), cid))
        oi, oerr = _import("openitems")
        for label in (self.label_suite(), "live"):
            rel = "records/open-items-check-%s.json" % label
            rec = self.counted(c, rel)
            if rec is None:
                continue
            if rec.get("verdict") != "pass":
                c.fail("fail", "%s verdict %s while an item is open%s" % (
                    rel, rec.get("verdict"),
                    (" (%s)" % "; ".join(map(str, rec.get("reasons") or []))) if rec.get("reasons") else ""))
            seen = []
            entry_ids = []
            for it in rec.get("items") or []:
                if not isinstance(it, dict):
                    c.fail("fail", "%s has an item entry that is not an object" % rel)
                    continue
                entry_ids.append(it.get("id"))
                for cs in it.get("cases") or []:
                    if isinstance(cs, dict):
                        seen.append((it.get("id"), cs.get("case_id"), cs))
            # Item entries are checked on their own, so an entry with no cases still counts.
            for item_id in sorted(set(entry_ids), key=str):
                if entry_ids.count(item_id) > 1:
                    c.fail("fail", "%s lists item %s twice" % (rel, item_id))
                if item_id not in by_id:
                    c.fail("fail", "%s lists item %s that is not an open item" % (rel, item_id))
            for item_id in sorted(set(by_id) - set(entry_ids), key=str):
                c.fail("fail", "%s omits item %s" % (rel, item_id))
            pairs = [(i, cid) for i, cid, _ in seen]
            for pair in sorted(set(pairs)):
                if pairs.count(pair) > 1:
                    c.fail("fail", "%s lists item %s case %s twice" % (rel, pair[0], pair[1]))
            for pair in sorted(required - set(pairs)):
                c.fail("fail", "%s omits item %s case %s" % (rel, pair[0], pair[1]))
            for pair in sorted(set(pairs) - required):
                c.fail("fail", "%s lists item %s case %s that no open item names" % (rel, pair[0], pair[1]))
            if oi is None:
                c.fail("unknown", oerr)
                continue
            for item_id, cid in sorted(required):
                out, _ = _records.load_json(self.p("outputs/%s/%s.json" % (label, cid)))
                try:
                    observed, reason = oi.safe_path_observed(by_id[item_id], out if isinstance(out, dict) else None,
                                                             self.brief)
                except Exception as e:
                    observed, reason = False, "safe_path_observed raised %s" % e
                stored = [cs.get("observed") for i, x, cs in seen if (i, x) == (item_id, cid)]
                if stored and stored[0] != observed:
                    c.reasons.append("open-items: %s stores observed %s for %s/%s, re-derived %s (re-derived decides)"
                                     % (rel, stored[0], item_id, cid, observed))
                if not observed:
                    c.fail("fail", "safe path not observed for item %s case %s in %s (%s)"
                           % (item_id, cid, label, reason))

    def check_residuals(self):
        c = self.checks["residuals"]
        doc, err = _records.load_json(self.p("residuals.json"))
        if not isinstance(doc, dict) or not isinstance(doc.get("residuals"), list):
            c.fail("missing", "residuals.json %s" % (err or "has no residuals list"))
            return
        for r in doc["residuals"]:
            if not isinstance(r, dict) or r.get("status") != "open":
                continue
            if r.get("kind") == "blocks-landing":
                c.fail("fail", "open blocks-landing residual %s" % r.get("id"))
            elif r.get("kind") == "lands-with-a-note":
                c.reasons.append("residuals: open lands-with-a-note residual %s (does not block)" % r.get("id"))
            else:
                c.fail("unknown", "residual %s has unknown kind %s" % (r.get("id"), r.get("kind")))

    def check_handover(self):
        c = self.checks["handover"]
        doc, err = _records.load_json(self.p("handover.json"))
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
            c.fail("missing", "handover.json %s" % (err or "has no items list"))
            return
        for it in doc["items"]:
            if not isinstance(it, dict):
                continue
            if it.get("status") == "done" and not it.get("run_record"):
                c.fail("fail", "item %s claims done without a bound run record" % it.get("id"))
            elif it.get("status") == "open":
                c.reasons.append("handover: open item %s (%s), listed, does not block"
                                 % (it.get("id"), it.get("what")))

    def check_package(self):
        c = self.checks["package-hash"]
        if self.package_sha is None:
            c.fail("missing", "package.sha256 missing")
            return
        try:
            now = _records.package_hash(self.p("package"))
        except (ValueError, OSError) as e:
            c.fail("fail", "package cannot be hashed (%s)" % e)
            return
        if now != self.package_sha:
            c.fail("fail", "package changed: package.sha256 %s, package/ now %s" % (self.package_sha, now))

    # ---- whole run -----------------------------------------------------
    def evaluate(self):
        compare, cerr = _import("compare")
        self.check_freeze()
        self.check_early()
        self.check_final(compare, cerr)
        self.check_suite(compare, cerr)
        self.check_stoprules()
        self.check_module("numbers")
        self.check_module("core")
        self.check_live(compare, cerr)
        self.check_model_calls()
        self.check_cold()
        self.check_clearance()
        self.check_open_items()
        self.check_residuals()
        self.check_handover()
        self.check_package()

        v, sv = self.brief.get("version"), self.brief.get("split_version")
        if not _is_int(v) or not _is_int(sv) or v != sv:
            self.run_reasons.append("brief version %s differs from split version %s" % (v, sv))

        for ex in self.brief.get("exemptions") or []:
            if not isinstance(ex, dict):
                self.run_reasons.append("exemption is not an object")
                continue
            name = ex.get("check")
            if not ex.get("alternative") or not ex.get("ratified_by"):
                self.run_reasons.append("exemption for %s lacks alternative or ratified_by" % name)
                continue
            if name not in self.checks:
                self.run_reasons.append("exemption names unknown check %s" % name)
                continue
            c = self.checks[name]
            c.result = "exempted"
            c.reasons = []
            c.per_process = {}
            c.reason = "exempted: alternative %s, ratified by %s" % (ex["alternative"], ex["ratified_by"])

        reasons = list(self.run_reasons)
        shared = list(self.run_reasons)
        for name in CHECK_NAMES:
            c = self.checks[name]
            if c.result in PASSING:
                # Listed, never blocking: open hand-over items, lands-with-a-note residuals,
                # stale dispositions, stored verdicts the re-derivation overturned.
                reasons.extend(c.reasons)
                continue
            reasons.extend(c.reasons or ["%s: %s" % (name, c.result)])
            if c.unscoped or not c.per_process:
                shared.extend(c.reasons or ["%s: %s" % (name, c.result)])
        processes = []
        for pid in self.processes:
            mine = list(shared)
            for name in CASE_SCOPED:
                c = self.checks[name]
                if c.result not in PASSING:
                    mine.extend(c.per_process.get(pid, []))
            processes.append({"process": pid, "status": "not done" if mine else "done", "reasons": mine})
        blocking = [n for n in CHECK_NAMES if self.checks[n].result not in PASSING]
        done = not self.run_reasons and not blocking and bool(processes) \
            and all(p["status"] == "done" for p in processes)
        return {
            "status": "done" if done else "not done",
            "verdict": "pass" if done else "fail",
            "reasons": reasons,
            "processes": processes,
            "package_sha256": self.package_sha,
            "manifest_version": self.version,
            "checks": [self.checks[n].to_json() for n in CHECK_NAMES],
        }


def gate(run):
    """Evaluate the run, write R/records/exit.json, append exit.written; return the record."""
    g = Gate(run)
    body = g.evaluate()
    header = _records.record_header(_records.SKILL_VERSION, _records.standard_input_hashes(run),
                                    {"package_sha256": g.package_sha})
    header.update(body)
    records = os.path.join(run, "records")
    if not os.path.isdir(records):
        os.makedirs(records)
    path = os.path.join(records, "exit.json")
    _records.write_record(path, header)
    log.append(run, "exit.written", {"path": "records/exit.json", "status": header["status"],
                                     "sha256": _records.sha256_file(path)})
    return header


def main(argv=None):
    parser = argparse.ArgumentParser(prog="exit_gate.py", description="Agent Build exit gate.")
    parser.add_argument("--run", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return _records.USAGE if e.code else _records.OK
    if not os.path.isdir(args.run):
        sys.stderr.write("--run is not a folder: %s\n" % args.run)
        return _records.USAGE
    rec = gate(args.run)
    for r in rec["reasons"]:
        print("- " + r)
    print("exit: %s" % rec["status"])
    return _records.OK if rec["status"] == "done" else _records.FAILED


if __name__ == "__main__":
    sys.exit(main())

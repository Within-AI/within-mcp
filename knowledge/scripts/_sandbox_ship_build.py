"""Stage 12 package builder library: the spec's listed files become one build context, and nothing else does.

Reads the spec (sandbox-ship-spec/v1) as plain YAML, uses only `package.root`, `package.files[]` and the optional
`package.build`, copies each listed source file to its `dest` under the package root inside the run's own folder,
generates a Dockerfile (default stack only) and a .dockerignore from the templates in sandbox-ship-stage/templates/,
computes the package hash and writes package.lock.json. Docker is neither needed nor used; a later, separate step
builds the image.

package.build ({dockerfiles: [dest, ...], compose: dest | null}) is optional. Without it (or with null) the builder
generates the one-image Dockerfile exactly as before. With it, the package brings its own Dockerfiles and compose
file as listed files; the builder generates no Dockerfile and instead checks them: every copy step takes only
listed files or a listed sub-folder (never the whole package, a parent, an absolute path or a wildcard), and every
compose service builds from the package root with a listed Dockerfile.

Package hash: sha256 over the sorted lines "dest<TAB>file-sha256" (dest normalised, each line ending in a newline),
one per file in package.files plus one each for the generated Dockerfile (default stack only) and .dockerignore
(dests Dockerfile and .dockerignore). A provided Dockerfile is a listed file and counts under its own dest. Same
spec and same file bytes always give the same hash. package_hash() below is the only implementation; the
cross-client scan and the clearance rule load this file and call it.
"""
import hashlib
import json
import os
import re
import shutil

SPEC_SCHEMA = "sandbox-ship-spec/v1"
LOCK_NAME = "package.lock.json"
DOCKERFILE_NAME = "Dockerfile"
DOCKERIGNORE_NAME = ".dockerignore"
GENERATED = (LOCK_NAME, DOCKERFILE_NAME, DOCKERIGNORE_NAME)
COPY_MARK = "@@COPY_LINES@@"
ALLOW_MARK = "@@ALLOW_LINES@@"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
TEMPLATE_DIR = os.path.join(PLUGIN_ROOT, "sandbox-ship-stage", "templates")
DOCKERFILE_TEMPLATE = os.path.join(TEMPLATE_DIR, "Dockerfile.template")
DOCKERIGNORE_TEMPLATE = os.path.join(TEMPLATE_DIR, "dockerignore.template")
WHOLE_FOLDER_SOURCES = {".", "./", "..", "*", "**", "/", "./*", "./."}
# Characters that would turn a destination into a pattern or a comment in the ignore file.
PATTERN_CHARS = set('*?[]!#\\"\n\r\t')


class BuildError(Exception):
    """A plain-words refusal: says what is wrong and what is needed."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_dest(dest):
    """A destination as the package holds it: normalised, forward slashes."""
    return os.path.normpath(dest).replace(os.sep, "/")


def provides_build(build):
    """True when the spec's package.build is given (the package brings its own Dockerfiles); absent or null is the
    default stack, where the builder generates the Dockerfile."""
    return build is not None


def generated_names(build=None):
    """The files the builder writes itself for this package.build value."""
    if provides_build(build):
        return (LOCK_NAME, DOCKERIGNORE_NAME)
    return GENERATED


def generated_hashes(package_root, build=None):
    """{name: sha256} of the generated files that join the package hash, as built in PACKAGE_ROOT: the Dockerfile
    and .dockerignore for the default stack, the .dockerignore alone when package.build is given (BUILD is the spec's
    package.build value, None when absent).

    Refuses in plain words when the package folder or a file is missing: the package has to be built first.
    """
    out = {}
    names = (DOCKERIGNORE_NAME,) if provides_build(build) else (DOCKERFILE_NAME, DOCKERIGNORE_NAME)
    for name in names:
        full = os.path.join(package_root or "", name)
        if not package_root or not os.path.isfile(full):
            raise BuildError("The built package is missing %s under %s. Build the package first "
                             "(scripts/sandbox-ship-build.py), then run this again." % (name, package_root))
        out[name] = sha256_file(full)
    return out


def package_hash(entries, generated):
    """The one package hash rule, called from the builder, the cross-client scan and the clearance rule.

    entries: iterable of (dest, file_sha256) for package.files. generated: {name: sha256} holding the generated
    .dockerignore and, for the default stack, the generated Dockerfile (generated_hashes reads them from the built
    package folder). One more line of the same form per generated file, dest "Dockerfile" and ".dockerignore", joins
    the file lines; a provided Dockerfile is already a file line under its own dest. Each dest is normalised, the
    lines "dest<TAB>sha256" are sorted, each is ended by a newline, and the sha256 of the UTF-8 text is returned.
    """
    rows = [(normalize_dest(d), s) for d, s in entries]
    rows.append((DOCKERIGNORE_NAME, generated[DOCKERIGNORE_NAME]))
    if DOCKERFILE_NAME in generated:
        rows.append((DOCKERFILE_NAME, generated[DOCKERFILE_NAME]))
    lines = sorted("%s\t%s" % (d, s) for d, s in rows)
    return sha256_bytes("".join(line + "\n" for line in lines).encode("utf-8"))


def _inside(child, parent):
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def load_spec(path):
    try:
        import yaml
    except ImportError:
        raise BuildError("PyYAML is needed to read the spec but is not importable (pip install pyyaml).")
    if not os.path.isfile(path):
        raise BuildError("The spec %s does not exist. Give the path to the run's sandbox-ship spec." % path)
    with open(path, "r", encoding="utf-8") as f:
        try:
            spec = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise BuildError("The spec %s is not readable YAML (%s)." % (path, exc))
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA:
        raise BuildError("The spec %s must be a mapping with schema: %s." % (path, SPEC_SCHEMA))
    return spec


def read_template(path, mark, what):
    if not os.path.isfile(path):
        raise BuildError("The %s template is missing at %s." % (what, path))
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if text.count(mark) != 1:
        raise BuildError("The %s template must contain %s exactly once (found %d)."
                         % (what, mark, text.count(mark)))
    return text


def _copy_sources(line):
    """Sources named by one COPY or ADD line (JSON-array form or shell form); [] for other lines."""
    stripped = line.strip()
    m = re.match(r"(?i)^(COPY|ADD)\s+(.*)$", stripped)
    if not m:
        return []
    rest = m.group(2).strip()
    if rest.startswith("["):
        try:
            items = json.loads(rest)
        except ValueError:
            raise BuildError("A copy step in the Dockerfile is not readable: %r." % stripped)
        return [str(i) for i in items[:-1]]
    tokens = [t for t in rest.split() if not t.startswith("--")]
    return tokens[:-1]


def refuse_whole_folder_copy(dockerfile_text):
    """Refuse any copy step whose source is the whole folder, a parent folder, or a wildcard of everything."""
    for line in dockerfile_text.splitlines():
        for src in _copy_sources(line):
            norm = src.strip()
            if (norm in WHOLE_FOLDER_SOURCES or norm.startswith("..") or norm.startswith("/")
                    or norm.startswith("./..") or norm.endswith("/..")):
                raise BuildError("Refused: a copy step takes %r, which is the whole folder or a parent folder. "
                                 "The Dockerfile may copy only the files the spec lists, one by one." % src)


def _clean_dest(dest):
    """A dest as package.build names it, compared with package.files dests after the same normalising."""
    return normalize_dest(dest) if isinstance(dest, str) and dest.strip() else None


def build_block_findings(pkg):
    """Plain findings on package.build's shape and that every name in it is a package.files destination; [] when it
    is absent, null, or sound. Shared by the builder and the spec check, so the two read the field the same way."""
    if not isinstance(pkg, dict) or pkg.get("build") is None:
        return []
    blk = pkg["build"]
    if not isinstance(blk, dict):
        return ["package.build must be a mapping {dockerfiles: [dest, ...], compose: dest or null}; leave it out "
                "(or null) to use the generated default Dockerfile"]
    out = []
    extra = sorted(str(k) for k in blk if k not in ("dockerfiles", "compose"))
    if extra:
        out.append("package.build has unknown key(s) %s; it holds only dockerfiles and compose" % ", ".join(extra))
    listed = set()
    for f in pkg.get("files") or []:
        if isinstance(f, dict) and _clean_dest(f.get("dest")):
            listed.add(_clean_dest(f["dest"]))
    dfs = blk.get("dockerfiles")
    if not isinstance(dfs, list) or not dfs:
        out.append("package.build.dockerfiles must be a non-empty list of package destinations, one per Dockerfile "
                   "the package brings")
    else:
        seen = set()
        for i, d in enumerate(dfs):
            norm = _clean_dest(d)
            if norm is None:
                out.append("package.build.dockerfiles entry %d is not a destination path; give the dest of a "
                           "Dockerfile listed in package.files" % (i + 1))
            elif norm not in listed:
                out.append("package.build.dockerfiles names %s, which is not a package.files destination; add the "
                           "Dockerfile to package.files with that dest, or remove it from package.build" % d)
            elif norm in seen:
                out.append("package.build.dockerfiles names %s twice; list each Dockerfile once" % d)
            seen.add(norm)
    if "compose" not in blk:
        out.append("package.build.compose is missing; give the dest of the compose file, or null when there is none")
    elif blk["compose"] is not None:
        norm = _clean_dest(blk["compose"])
        if norm is None:
            out.append("package.build.compose is not a destination path; give the dest of the compose file listed "
                       "in package.files, or null")
        elif norm not in listed:
            out.append("package.build.compose names %s, which is not a package.files destination; add the compose "
                       "file to package.files with that dest, or set compose to null" % blk["compose"])
    return out


def _instructions(text):
    """(line number, stage label, keyword, arguments) per Dockerfile instruction, with continuation lines joined and
    comment lines dropped. The stage label is "build stage N" plus the stage's AS name when it has one."""
    escape = "\\"
    lines = text.splitlines()
    for raw in lines:
        m = re.match(r"^\s*#\s*escape\s*=\s*(\S)\s*$", raw, re.IGNORECASE)
        if m:
            escape = m.group(1)
            break
        if not re.match(r"^\s*#\s*\w+\s*=", raw):
            break
    out, stage, stage_no = [], "before any FROM", 0
    i = 0
    while i < len(lines):
        start = i + 1
        cur = lines[i]
        i += 1
        if not cur.strip() or cur.strip().startswith("#"):
            continue
        parts = []
        while True:
            s = cur.rstrip()
            if not s.endswith(escape):
                parts.append(s)
                break
            parts.append(s[:-1])
            while i < len(lines) and lines[i].strip().startswith("#"):
                i += 1  # a comment line inside a continuation is dropped; the continuation goes on
            if i >= len(lines):
                break
            cur = lines[i]
            i += 1
        joined = " ".join(parts).strip()
        if not joined:
            continue
        m = re.match(r"^(\S+)\s*(.*)$", joined)
        keyword, rest = m.group(1).upper(), m.group(2).strip()
        if keyword == "FROM":
            stage_no += 1
            named = re.search(r"(?i)\sAS\s+(\S+)\s*$", " " + rest)
            stage = "build stage %d%s" % (stage_no, (" '%s'" % named.group(1)) if named else "")
        out.append((start, stage, keyword, rest))
    return out


def _split_copy(rest):
    """(flags, sources) of one COPY or ADD instruction's arguments, JSON-array form or shell form."""
    flags = []
    tokens = rest.split()
    while tokens and tokens[0].startswith("--"):
        flags.append(tokens.pop(0))
    body = rest
    for f in flags:
        body = body.split(f, 1)[1]
    body = body.strip()
    if body.startswith("["):
        try:
            items = json.loads(body)
        except ValueError:
            return flags, None
        if not isinstance(items, list) or len(items) < 2:
            return flags, None
        return flags, [str(x) for x in items[:-1]]
    if len(tokens) < 2:
        return flags, None
    return flags, tokens[:-1]


def provided_dockerfile_findings(dest, text, listed):
    """Plain findings for one provided Dockerfile (DEST in the package, TEXT its bytes as text), every build stage:
    each COPY or ADD source other than a --from copy is a listed destination, or a sub-folder (never the package root)
    that holds at least one listed destination, and no ONBUILD instruction appears (it would defer a step past these
    rules). LISTED is the set of normalised package.files dests."""
    out = []
    for line, stage, keyword, rest in _instructions(text):
        if keyword == "ONBUILD":
            out.append("Dockerfile %s, line %d (%s): ONBUILD %s defers an instruction to a later build, where the copy "
                       "rules cannot check it; a package build has no reason to defer, so remove the ONBUILD line"
                       % (dest, line, stage, rest.split()[0].upper() if rest.split() else "(empty)"))
            continue
        if keyword not in ("COPY", "ADD"):
            continue
        where = "Dockerfile %s, line %d (%s)" % (dest, line, stage)
        flags, sources = _split_copy(rest)
        if any(f.lower().startswith("--from=") for f in flags):
            continue  # copies out of another build stage or image, not out of the package
        if sources is None:
            out.append("%s: the %s step is not readable; write it as %s <source> ... <target>" % (where, keyword, keyword))
            continue
        for src in sources:
            s = src.strip()
            fix = ("copy only files listed in package.files, or a sub-folder that holds listed files, by their "
                   "package path")
            if s.startswith("<<"):
                out.append("%s: %s %s is an inline file the check cannot read; put the file in package.files and "
                           "copy it by its package path" % (where, keyword, s))
            elif s.startswith("/"):
                out.append("%s: %s takes %r, an absolute path; %s" % (where, keyword, s, fix))
            elif ".." in s:
                out.append("%s: %s takes %r, which reaches a parent folder; %s" % (where, keyword, s, fix))
            elif any(c in s for c in "*?["):
                out.append("%s: %s takes %r, a wildcard; name each file or sub-folder instead (%s)"
                           % (where, keyword, s, fix))
            else:
                norm = normalize_dest(s)
                if norm in (".", ""):
                    out.append("%s: %s takes %r, the whole package; %s" % (where, keyword, s, fix))
                elif norm not in listed and not any(d.startswith(norm + "/") for d in listed):
                    out.append("%s: %s takes %r, which is neither a package.files destination nor a sub-folder "
                               "holding one; add it to package.files, or %s" % (where, keyword, s, fix))
    return out


def compose_findings(dest, text, dockerfiles):
    """Plain findings for the provided compose file (DEST in the package, TEXT its bytes as text): every service that
    builds uses the package root as its build context and a Dockerfile listed in package.build.dockerfiles."""
    try:
        import yaml
    except ImportError:
        raise BuildError("PyYAML is needed to read the compose file but is not importable (pip install pyyaml).")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ["compose file %s is not readable YAML (%s); fix the file" % (dest, exc)]
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict) or not services:
        return ["compose file %s has no services mapping; give each service under services:" % dest]
    base = os.path.dirname(dest)
    out = []
    for name in sorted(services, key=str):
        svc = services[name]
        if not isinstance(svc, dict) or "build" not in svc:
            continue
        where = "compose file %s, service %s" % (dest, name)
        build = svc["build"]
        if isinstance(build, str):
            build = {"context": build}
        if not isinstance(build, dict):
            out.append("%s: build must be a context path or a mapping with context and dockerfile" % where)
            continue
        for key in ("dockerfile_inline", "additional_contexts"):
            if key in build:
                out.append("%s: build.%s is not accepted; build from the package root with a listed Dockerfile "
                           "and remove %s" % (where, key, key))
        context = str(build.get("context", "."))
        ctx = None
        if (context.startswith("/") or "://" in context or context.startswith("git@")
                or normalize_dest(os.path.join(base, context)) != "."):
            out.append("%s: build.context %r does not resolve to the package root; set context to %r so it points "
                       "at the package root" % (where, context, os.path.relpath(".", base) if base else "."))
        else:
            ctx = "."
        dockerfile = str(build.get("dockerfile", DOCKERFILE_NAME))
        if ctx is not None:
            resolved = normalize_dest(dockerfile)
            if dockerfile.startswith("/") or resolved not in dockerfiles:
                out.append("%s: dockerfile %r is not listed in package.build.dockerfiles; list it there (and in "
                           "package.files), or point the service at a listed Dockerfile" % (where, dockerfile))
    return out


def plan_files(spec, run_root):
    """Validate the spec's package section against the run root. Returns (package_root, [dict(src_abs, dest)])."""
    pkg = spec.get("package")
    if not isinstance(pkg, dict) or not pkg.get("root"):
        raise BuildError("The spec has no package.root. Say which folder inside the run holds the package.")
    run_root = os.path.realpath(run_root)
    root_rel = str(pkg["root"])
    if os.path.isabs(root_rel):
        raise BuildError("package.root %r is an absolute path. It must be a folder inside the run's own folder."
                         % root_rel)
    package_root = os.path.realpath(os.path.join(run_root, root_rel))
    if package_root == run_root or not _inside(package_root, run_root):
        raise BuildError("package.root %r resolves outside the run's own folder (or is the run folder itself). "
                         "Choose a sub-folder of the run." % root_rel)
    files = pkg.get("files")
    if not isinstance(files, list) or not files:
        raise BuildError("The spec lists no package.files. List every file the package needs, each with src and dest.")
    generated = generated_names(pkg.get("build"))
    planned, seen = [], {}
    for i, item in enumerate(files):
        if not isinstance(item, dict) or not item.get("src") or not item.get("dest"):
            raise BuildError("package.files entry %d needs both src and dest." % (i + 1))
        src, dest = str(item["src"]), str(item["dest"])
        if os.path.isabs(src):
            raise BuildError("Source %r is an absolute path. Sources are paths inside the run's own folder." % src)
        src_abs = os.path.realpath(os.path.join(run_root, src))
        if not _inside(src_abs, run_root):
            raise BuildError("Source %r resolves outside the run's own folder. Copy it into the run first." % src)
        if _inside(src_abs, package_root):
            raise BuildError("Source %r is inside the package folder. Sources must come from outside it, because "
                             "the package folder is rebuilt from the spec." % src)
        if not os.path.isfile(src_abs):
            raise BuildError("Source file %r is missing (or is not a file). Add it or remove it from package.files."
                             % src)
        if os.path.isabs(dest) or any(c in PATTERN_CHARS for c in dest) or dest != dest.strip():
            raise BuildError("Destination %r is not a plain relative path (no absolute path, wildcard, quote, "
                             "backslash, or surrounding space)." % dest)
        norm = os.path.normpath(dest)
        if norm in (".", "") or norm == ".." or norm.startswith(".." + os.sep):
            raise BuildError("Destination %r escapes the package folder. Use a path under it." % dest)
        norm = norm.replace(os.sep, "/")
        if not _inside(os.path.join(package_root, norm), package_root):
            raise BuildError("Destination %r escapes the package folder. Use a path under it." % dest)
        if norm in generated:
            raise BuildError("Destination %r is a file the builder generates. Choose another name." % dest)
        if norm in seen:
            raise BuildError("Two files share the destination %r (sources %r and %r). Each dest must be unique."
                             % (norm, seen[norm], src))
        seen[norm] = src
        planned.append({"src": src, "src_abs": src_abs, "dest": norm})
    # a destination that is a folder for another destination cannot also be a file
    dests = sorted(seen)
    for d in dests:
        for other in dests:
            if other.startswith(d + "/"):
                raise BuildError("Destination %r is a file and also a folder for %r. Rename one." % (d, other))
    planned.sort(key=lambda p: p["dest"])
    problems = build_block_findings(pkg)
    if problems:
        raise BuildError("The spec's package.build is refused: %s." % "; ".join(problems))
    return package_root, planned


def _read_text(p, what):
    with open(p["src_abs"], "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise BuildError("The %s %s (source %r) is not UTF-8 text. Save it as text." % (what, p["dest"], p["src"]))


def check_provided_build(build, planned):
    """Refuse, in one plain message naming each file and line or service, any provided Dockerfile or compose file
    that would take more than the listed files into an image."""
    by_dest = {p["dest"]: p for p in planned}
    listed = set(by_dest)
    dockerfiles = [normalize_dest(d) for d in build["dockerfiles"]]
    problems = []
    for d in dockerfiles:
        problems.extend(provided_dockerfile_findings(d, _read_text(by_dest[d], "Dockerfile"), listed))
    if build.get("compose") is not None:
        c = normalize_dest(build["compose"])
        problems.extend(compose_findings(c, _read_text(by_dest[c], "compose file"), set(dockerfiles)))
    if problems:
        raise BuildError("Refused: the package's own build files take more than the listed files, or do not "
                         "build from the package root.\n  - %s" % "\n  - ".join(problems))


def derive(spec, run_root):
    """Everything the package must contain, computed from the spec and the source bytes; nothing is written."""
    package_root, planned = plan_files(spec, run_root)
    for p in planned:
        p["sha256"] = sha256_file(p["src_abs"])
    build = spec["package"].get("build")
    if provides_build(build):
        return _derive_provided(package_root, planned, build)
    copy_lines = "\n".join('COPY ["%s", "%s"]' % (p["dest"], p["dest"]) for p in planned)
    allow_lines = "\n".join("!%s" % p["dest"] for p in planned)
    df_template = read_template(DOCKERFILE_TEMPLATE, COPY_MARK, "Dockerfile")
    ig_template = read_template(DOCKERIGNORE_TEMPLATE, ALLOW_MARK, "dockerignore")
    refuse_whole_folder_copy(df_template.replace(COPY_MARK, ""))
    dockerfile = df_template.replace(COPY_MARK, copy_lines)
    refuse_whole_folder_copy(dockerfile)
    dockerignore = ig_template.replace(ALLOW_MARK, allow_lines)
    pkg_hash = package_hash([(p["dest"], p["sha256"]) for p in planned],
                            {DOCKERFILE_NAME: sha256_bytes(dockerfile.encode("utf-8")),
                             DOCKERIGNORE_NAME: sha256_bytes(dockerignore.encode("utf-8"))})
    lock = {
        "schema": "sandbox-ship-package-lock/v1",
        "package_sha256": pkg_hash,
        "files": [{"dest": p["dest"], "sha256": p["sha256"]} for p in planned],
        "dockerfile_sha256": sha256_bytes(dockerfile.encode("utf-8")),
        "dockerignore_sha256": sha256_bytes(dockerignore.encode("utf-8")),
    }
    lock_text = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    return {"package_root": package_root, "files": planned, "dockerfile": dockerfile,
            "dockerignore": dockerignore, "lock": lock, "lock_text": lock_text, "package_sha256": pkg_hash}


def _derive_provided(package_root, planned, build):
    """derive() for a package that brings its own Dockerfiles: checked, not generated; .dockerignore and the lock as
    usual. The lock records package.build in place of a generated Dockerfile's sha256."""
    check_provided_build(build, planned)
    ig_template = read_template(DOCKERIGNORE_TEMPLATE, ALLOW_MARK, "dockerignore")
    dockerignore = ig_template.replace(ALLOW_MARK, "\n".join("!%s" % p["dest"] for p in planned))
    ig_sha = sha256_bytes(dockerignore.encode("utf-8"))
    pkg_hash = package_hash([(p["dest"], p["sha256"]) for p in planned], {DOCKERIGNORE_NAME: ig_sha})
    lock = {
        "schema": "sandbox-ship-package-lock/v1",
        "package_sha256": pkg_hash,
        "files": [{"dest": p["dest"], "sha256": p["sha256"]} for p in planned],
        "build": {"dockerfiles": [normalize_dest(d) for d in build["dockerfiles"]],
                  "compose": normalize_dest(build["compose"]) if build.get("compose") is not None else None},
        "dockerignore_sha256": ig_sha,
    }
    lock_text = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    return {"package_root": package_root, "files": planned, "dockerfile": None, "dockerignore": dockerignore,
            "lock": lock, "lock_text": lock_text, "package_sha256": pkg_hash, "generated": generated_names(build)}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def build(spec, run_root):
    """Write the package. The package folder is emptied first so nothing unlisted can be in it."""
    d = derive(spec, run_root)
    root = d["package_root"]
    if os.path.lexists(root):
        if not os.path.isdir(root) or os.path.islink(root):
            raise BuildError("package.root %r exists and is not a plain folder. Remove it or choose another."
                             % os.path.relpath(root, run_root))
        shutil.rmtree(root)
    os.makedirs(root)
    for p in d["files"]:
        with open(p["src_abs"], "rb") as f:
            data = f.read()
        if sha256_bytes(data) != p["sha256"]:
            raise BuildError("Source %r changed while it was being copied. Run the build again." % p["src"])
        _write(os.path.join(root, p["dest"]), data)
    if d["dockerfile"] is not None:
        _write(os.path.join(root, DOCKERFILE_NAME), d["dockerfile"].encode("utf-8"))
    _write(os.path.join(root, DOCKERIGNORE_NAME), d["dockerignore"].encode("utf-8"))
    _write(os.path.join(root, LOCK_NAME), d["lock_text"].encode("utf-8"))
    return d


def verify(spec, run_root):
    """Re-derive the package from the spec and compare it with the folder on disk. Returns a list of findings."""
    d = derive(spec, run_root)
    root = d["package_root"]
    problems = []
    if not os.path.isdir(root):
        return ["The package folder %s does not exist. Run scripts/sandbox-ship-build.py first."
                % os.path.relpath(root, run_root)]
    expected = {p["dest"]: p for p in d["files"]}
    on_disk = set()
    for base, dirs, names in os.walk(root):
        for n in names:
            on_disk.add(os.path.relpath(os.path.join(base, n), root).replace(os.sep, "/"))
    for dest, p in sorted(expected.items()):
        full = os.path.join(root, dest)
        if dest not in on_disk:
            problems.append("Listed file %s is missing from the package." % dest)
        elif sha256_file(full) != p["sha256"]:
            problems.append("Package file %s does not match its source %s (edited by hand, or the source changed). "
                            "Rebuild it." % (dest, p["src"]))
    generated = d.get("generated", GENERATED)
    allowed = set(expected) | set(generated)
    for extra in sorted(on_disk - allowed):
        problems.append("Package file %s is not listed in the spec. Nothing unlisted may be in the package." % extra)
    wants = [(DOCKERIGNORE_NAME, d["dockerignore"]), (LOCK_NAME, d["lock_text"])]
    if d["dockerfile"] is not None:
        wants.insert(0, (DOCKERFILE_NAME, d["dockerfile"]))
    for name, want in wants:
        full = os.path.join(root, name)
        if name not in on_disk:
            problems.append("%s is missing from the package." % name)
        elif open(full, "rb").read() != want.encode("utf-8"):
            problems.append("%s differs from what the spec and templates produce. Rebuild the package." % name)
    return problems

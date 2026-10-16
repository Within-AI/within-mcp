"""Stage 12 (sandbox-ship): who the current client is, shared by the cross-client scan and the spec check.

The run header is the trusted source: the first `run.opened` event of the run's events.jsonl carries `client`
(a name, or a mapping with `name`). The spec's client.name must equal it. The pinned snapshot is not required to
name the client; when it does hold a top-level `client` that disagrees with the header, that is refused too.
Events are read through stage 11's reader in checks/_prebuild_sim.py, the way the spec check reads them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402


class ClientError(Exception):
    """One plain sentence: what is wrong and what to correct."""


def _name(v):
    v = v.get("name") if isinstance(v, dict) else v
    return v.strip() if isinstance(v, str) and v.strip() else None


def header_client(events):
    opened = next((e for e in events if e.get("type") == "run.opened"), None)
    name = _name((opened or {}).get("client"))
    if name is None:
        raise ClientError("the run header (the run.opened event in events.jsonl) records no client; record the "
                          "client there, because the scan and the spec check take the current client from it")
    return name


def current_client(events, spec_name, snapshot_doc=None):
    """The header's client name. Raises ClientError when the header has none, the spec's client.name differs from
    it, or the snapshot holds a top-level client that differs from it."""
    name = header_client(events)
    if (spec_name or "").strip() != name:
        raise ClientError("the spec's client.name does not match the client the run header records; correct "
                          "client.name in the spec")
    snap = _name(snapshot_doc.get("client")) if isinstance(snapshot_doc, dict) else None
    if snap is not None and snap != name:
        raise ClientError("the client snapshot records a different client than the run header; pin the right "
                          "snapshot")
    return name


def events_for(root, who):
    return pbs.require_events(root, who)

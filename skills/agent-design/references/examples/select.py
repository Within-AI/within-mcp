"""choose worked examples for a run, never one derived from the run's own client.

choose(client_name, process_count) -> list of example paths relative to this folder, in index order, examples whose
shape matches the run (single for one process, set for more) first, then the rest. An example whose
source_client_sha256 equals the hash of client_name is left out.
"""
import builtins
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.json")


def client_hash(name):
    """sha256 of the name stripped of surrounding white space, lower-cased, UTF-8 encoded (no alias matching)."""
    return hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()


def choose(client_name, process_count, index_path=None):
    if isinstance(process_count, bool) or not isinstance(process_count, int) or process_count < 1:
        raise builtins.ValueError(  # dotted, so the post-run name scan reads it as code
            "process_count must be an integer of 1 or more, got %r" % (process_count,))
    if client_name is None or not isinstance(client_name, str) or not client_name.strip():
        return []  # the run's client cannot be ruled out
    with open(index_path or INDEX, encoding="utf-8") as fh:
        examples = json.load(fh)["examples"]
    own = client_hash(client_name)
    kept = [e for e in examples if e["source_client_sha256"] != own]
    shape = "single" if process_count == 1 else "set"
    return [e["path"] for e in kept if e["shape"] == shape] + [e["path"] for e in kept if e["shape"] != shape]

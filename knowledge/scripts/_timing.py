"""UTC attribution with monotonic duration; missing measurements remain unknown."""
from datetime import datetime, timezone
import time


def start():
    return datetime.now(timezone.utc).isoformat(), time.perf_counter()


def finish(clock):
    return {"started_at": clock[0], "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - clock[1]) * 1000, 3)}

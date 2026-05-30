import datetime
import threading

from scheduler import Status

PIPELINE_RUNS = {}
PIPELINE_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def register_pipeline(pipeline_id: str, requested_tasks: list):
    with PIPELINE_LOCK:
        PIPELINE_RUNS[pipeline_id] = {
            "pipeline_id": pipeline_id,
            "status": "RUNNING",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "requested_tasks": requested_tasks,
            "tasks": [],
        }


def update_pipeline(pipeline_id: str, snapshot: dict):
    with PIPELINE_LOCK:
        run = PIPELINE_RUNS.setdefault(pipeline_id, {"pipeline_id": pipeline_id})
        tasks = snapshot.get("tasks", [])
        run["tasks"] = tasks
        run["updated_at"] = _utc_now()
        if tasks and all(t.get("status") in (Status.COMPLETED.value, Status.FAILED.value) for t in tasks):
            run["status"] = "FAILED" if any(t.get("status") == Status.FAILED.value for t in tasks) else "COMPLETED"
        elif any(t.get("status") == Status.RUNNING.value for t in tasks):
            run["status"] = "RUNNING"
        else:
            run["status"] = "PENDING"


def get_pipeline_runs() -> list:
    with PIPELINE_LOCK:
        return sorted(PIPELINE_RUNS.values(), key=lambda r: r.get("created_at", ""), reverse=True)


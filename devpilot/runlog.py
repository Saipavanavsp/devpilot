import json
import os
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Event = dict[str, Any]
Listener = Callable[[Event], None]


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunLog:
    """The JSON record of one run, rewritten after every change so a crash loses nothing.

    Listeners receive each change as an event; the web UI uses them for live updates.
    """

    def __init__(
        self,
        runs_dir: Path,
        *,
        task: str,
        model: str,
        run_id: str | None = None,
        listeners: Iterable[Listener] = (),
    ) -> None:
        self.run_id = run_id or new_run_id()
        self.path = runs_dir / f"{self.run_id}.json"
        self._listeners = list(listeners)
        self.data: dict[str, Any] = {
            "run_id": self.run_id,
            "task": task,
            "model": model,
            "status": "running",
            "started_at": _now(),
            "finished_at": None,
            "plan": [],
            "steps": [],
            "usage": {},
            "cost_usd": 0.0,
            "summary": None,
        }
        runs_dir.mkdir(parents=True, exist_ok=True)
        self._save()
        self._emit("started", {"run_id": self.run_id, "task": task, "model": model})

    def update(self, **fields: Any) -> None:
        self.data.update(fields)
        self._save()
        self._emit("update", fields)

    def set_plan(self, plan: list[str]) -> None:
        self.data["plan"] = plan
        self._save()
        self._emit("plan", {"plan": plan})

    def add_step(self, step: dict[str, Any]) -> None:
        step = {"timestamp": _now(), **step}
        self.data["steps"].append(step)
        self._save()
        self._emit("step", step)

    def finish(self, status: str, **fields: Any) -> None:
        self.data.update(fields, status=status, finished_at=_now())
        self._save()
        self._emit("finished", {k: v for k, v in self.data.items() if k != "steps"})

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, "data": payload}
        for listener in self._listeners:
            listener(event)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

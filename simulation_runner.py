"""
Tracks background simulation runs so the dashboard can start one and poll
its progress, instead of requiring someone to open a terminal.

Deliberately simple (in-memory, single-process) — this is a demo/ops
convenience, not a durable job queue. Runs are lost on server restart,
and only one can be active at a time to keep it predictable.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from simulator import run_simulation

# Three fixed presets, matching what used to be documented as bash examples.
PRESETS = {
    "quick": {
        "label": "Quick smoke test",
        "description": "1 vehicle, 10 readings, sent instantly. Confirms the pipeline works.",
        "vehicles": 1, "readings": 10, "interval": 0, "anomaly_rate": 0.05,
    },
    "pilot": {
        "label": "Pilot-scale load test",
        "description": "50 vehicles, 100 readings each, realistic 10s cadence. Matches GEE774's pilot size. Takes ~15-20 minutes.",
        "vehicles": 50, "readings": 100, "interval": 10.0, "anomaly_rate": 0.05,
    },
    "realtime": {
        "label": "Real-time demo",
        "description": "5 vehicles, 30 readings, realistic 10s cadence. Good length for a live demo — about 5 minutes.",
        "vehicles": 5, "readings": 30, "interval": 10.0, "anomaly_rate": 0.08,
    },
}

_runs: dict[str, dict] = {}
_active_run_id: str | None = None


def get_active_run_id() -> str | None:
    return _active_run_id


def get_run(run_id: str) -> dict | None:
    return _runs.get(run_id)


async def start_run(profile: str, admin_key: str) -> str:
    global _active_run_id

    if profile not in PRESETS:
        raise ValueError(f"Unknown profile '{profile}'. Choose from: {list(PRESETS.keys())}")

    if _active_run_id is not None and _runs.get(_active_run_id, {}).get("status") == "running":
        raise RuntimeError("A simulation is already running. Wait for it to finish first.")

    run_id = str(uuid.uuid4())
    preset = PRESETS[profile]

    _runs[run_id] = {
        "run_id": run_id,
        "profile": profile,
        "label": preset["label"],
        "status": "running",
        "log": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    _active_run_id = run_id

    def log(message: str):
        _runs[run_id]["log"].append(message)

    async def _execute():
        try:
            await run_simulation(
                vehicles=preset["vehicles"],
                readings=preset["readings"],
                interval=preset["interval"],
                anomaly_rate=preset["anomaly_rate"],
                admin_key=admin_key,
                log=log,
            )
            _runs[run_id]["status"] = "completed"
        except Exception as e:
            log(f"ERROR: {e}")
            _runs[run_id]["status"] = "failed"
        finally:
            _runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_execute())
    return run_id

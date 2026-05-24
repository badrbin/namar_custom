from __future__ import annotations

from .server_runtime import run_scheduler_script

def scheduled_sync_material_request_state_duration():
    return run_scheduler_script("scheduled_sync_material_request_state_duration")

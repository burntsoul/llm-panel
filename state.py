# state.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from config import settings


def _state_path() -> Path:
    return Path(settings.STATE_PATH)


def load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"maintenance_mode": settings.MAINTENANCE_DEFAULT}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"maintenance_mode": settings.MAINTENANCE_DEFAULT}


def save_state(state: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def get_maintenance_mode() -> bool:
    return bool(load_state().get("maintenance_mode", settings.MAINTENANCE_DEFAULT))


def set_maintenance_mode(value: bool) -> None:
    st = load_state()
    st["maintenance_mode"] = bool(value)
    save_state(st)


def toggle_maintenance_mode() -> bool:
    new_value = not get_maintenance_mode()
    set_maintenance_mode(new_value)
    return new_value

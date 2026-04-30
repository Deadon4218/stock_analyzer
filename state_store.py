"""
Persistent state across GitHub Actions runs.
Stored in data/state.json — committed back to repo by workflows.
"""
import json
import os
from pathlib import Path

STATE_FILE = Path(os.environ.get("STATE_PATH", "data/state.json"))
MAX_KEYS = 1000  # cap to prevent unbounded growth


def _load() -> dict:
    if not STATE_FILE.exists():
        return {"analyzed_keys": [], "last_telegram_update_id": 0}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def _save(data: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_analyzed_keys() -> set[str]:
    return set(_load().get("analyzed_keys", []))


def add_analyzed_keys(new_keys: set[str]):
    state = _load()
    keys = state.get("analyzed_keys", [])
    keys = list(set(keys) | new_keys)
    # Keep only the most recent MAX_KEYS (preserves order via timestamp suffix)
    keys = sorted(keys)[-MAX_KEYS:]
    state["analyzed_keys"] = keys
    _save(state)


def get_last_update_id() -> int:
    return _load().get("last_telegram_update_id", 0)


def set_last_update_id(update_id: int):
    state = _load()
    state["last_telegram_update_id"] = update_id
    _save(state)


def get_cooldown_ts(chat_id: int, key: str) -> float:
    """Return last-action unix ts for (chat_id, key) or 0."""
    cd = _load().get("cooldowns", {})
    return float(cd.get(f"{chat_id}:{key}", 0))


def set_cooldown_ts(chat_id: int, key: str, ts: float):
    state = _load()
    cd = state.setdefault("cooldowns", {})
    cd[f"{chat_id}:{key}"] = ts
    _save(state)


def get_last_run_ts(label: str) -> float:
    """Last successful run timestamp for a labeled job (Morning/Evening)."""
    runs = _load().get("last_runs", {})
    return float(runs.get(label, 0))


def set_last_run_ts(label: str, ts: float):
    state = _load()
    runs = state.setdefault("last_runs", {})
    runs[label] = ts
    _save(state)

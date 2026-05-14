"""
Persistent state across runs.
Backed by Upstash Redis (see redis_store.py). Same public API as before.

Redis layout:
  state:analyzed_keys      — Set of "{ticker}:{ts}" signal IDs
  state:last_update_id     — String, last Telegram update_id processed
  state:cooldowns          — Hash, field "{chat_id}:{key}" → unix ts
  state:last_runs          — Hash, field label → unix ts
"""
import redis_store as r

MAX_KEYS = 1000  # cap to prevent unbounded growth

_K_ANALYZED = "state:analyzed_keys"
_K_LAST_UPDATE = "state:last_update_id"
_K_COOLDOWNS = "state:cooldowns"
_K_LAST_RUNS = "state:last_runs"


def get_analyzed_keys() -> set[str]:
    return r.smembers(_K_ANALYZED)


def add_analyzed_keys(new_keys: set[str]):
    if not new_keys:
        return
    r.sadd(_K_ANALYZED, *new_keys)
    # Cap the set if it grows past MAX_KEYS. Trim oldest (lex-sorted) members.
    # NOTE: signal IDs contain timestamps so lex sort ≈ chronological sort.
    if r.scard(_K_ANALYZED) > MAX_KEYS:
        all_keys = sorted(r.smembers(_K_ANALYZED))
        to_remove = all_keys[: len(all_keys) - MAX_KEYS]
        if to_remove:
            r.srem(_K_ANALYZED, *to_remove)


def get_last_update_id() -> int:
    val = r.get(_K_LAST_UPDATE)
    return int(val) if val else 0


def set_last_update_id(update_id: int):
    r.set(_K_LAST_UPDATE, str(update_id))


def get_cooldown_ts(chat_id: int, key: str) -> float:
    val = r.hget(_K_COOLDOWNS, f"{chat_id}:{key}")
    return float(val) if val else 0.0


def set_cooldown_ts(chat_id: int, key: str, ts: float):
    r.hset(_K_COOLDOWNS, f"{chat_id}:{key}", str(ts))


def get_last_run_ts(label: str) -> float:
    val = r.hget(_K_LAST_RUNS, label)
    return float(val) if val else 0.0


def set_last_run_ts(label: str, ts: float):
    r.hset(_K_LAST_RUNS, label, str(ts))

"""
Thin wrapper over Upstash Redis REST API.

Why REST instead of redis-py: serverless functions (Vercel) need a stateless
connection model. Upstash REST is a single HTTPS POST per command — no
connection pooling, no socket lifecycle to manage.

Required env vars:
  UPSTASH_REDIS_REST_URL    — https://xxx.upstash.io
  UPSTASH_REDIS_REST_TOKEN  — bearer token from Upstash console

All command helpers return the parsed "result" field from Upstash.
Errors raise RedisError so callers can handle them explicitly.
"""
from __future__ import annotations  # avoid set() func shadowing set type in annotations

import builtins
import json
import os
from typing import Any, Optional

import requests

# Alias for the builtin set type — our module-level `set()` Redis command
# shadows it at runtime, so internal code uses _set instead.
_set = builtins.set

_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
_TIMEOUT = 10


class RedisError(RuntimeError):
    pass


def _check_env():
    global _URL, _TOKEN
    if not _URL:
        _URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    if not _TOKEN:
        _TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if not _URL or not _TOKEN:
        raise RedisError(
            "Missing UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN env vars"
        )


def _call(cmd: list[Any]) -> Any:
    """Execute a single Redis command via Upstash REST API.

    cmd is a list like ["SET", "key", "value"] or ["HGETALL", "user:123"].
    Returns the unwrapped "result" value, or raises RedisError on failure.

    Retries up to 3 times on network/SSL timeouts (transient blips). Does
    NOT retry on HTTP errors or application-level Upstash errors — those
    are real failures we want to surface.
    """
    _check_env()
    body = [str(x) for x in cmd]
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(
                _URL,
                headers={"Authorization": f"Bearer {_TOKEN}"},
                json=body,
                timeout=_TIMEOUT,
            )
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt == 2:
                raise RedisError(f"Upstash network failure after 3 attempts: {e}") from e
            continue
        except requests.RequestException as e:
            raise RedisError(f"Upstash request failed: {e}") from e

    if r.status_code != 200:
        raise RedisError(f"Upstash HTTP {r.status_code}: {r.text[:200]}")

    payload = r.json()
    if "error" in payload:
        raise RedisError(f"Upstash error: {payload['error']}")
    return payload.get("result")


# ----- Strings -----

def get(key: str) -> Optional[str]:
    return _call(["GET", key])


def set(key: str, value: str) -> None:
    _call(["SET", key, value])


def setnx(key: str, value: str) -> bool:
    return bool(_call(["SETNX", key, value]))


def delete(key: str) -> int:
    return _call(["DEL", key])


# ----- JSON helpers (most fields we store are objects) -----

def get_json(key: str, default: Any = None) -> Any:
    raw = get(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def set_json(key: str, value: Any) -> None:
    set(key, json.dumps(value, separators=(",", ":")))


# ----- Sets (used for analyzed_keys, watchlist members) -----

def sadd(key: str, *members: str) -> int:
    if not members:
        return 0
    return _call(["SADD", key, *members])


def srem(key: str, *members: str) -> int:
    if not members:
        return 0
    return _call(["SREM", key, *members])


def smembers(key: str) -> set[str]:
    result = _call(["SMEMBERS", key]) or []
    return _set(result)


def sismember(key: str, member: str) -> bool:
    return bool(_call(["SISMEMBER", key, member]))


def scard(key: str) -> int:
    return _call(["SCARD", key]) or 0


# ----- Hashes (used for cooldowns, last_runs, user records) -----

def hget(key: str, field: str) -> Optional[str]:
    return _call(["HGET", key, field])


def hset(key: str, field: str, value: str) -> int:
    return _call(["HSET", key, field, value])


def hgetall(key: str) -> dict[str, str]:
    """Returns a dict. Upstash returns a flat [k,v,k,v,...] list; we zip it."""
    flat = _call(["HGETALL", key]) or []
    if isinstance(flat, dict):
        return flat
    return dict(zip(flat[::2], flat[1::2]))


def hdel(key: str, *fields: str) -> int:
    if not fields:
        return 0
    return _call(["HDEL", key, *fields])


# ----- Lists (used for analyses log) -----

def lpush(key: str, *values: str) -> int:
    if not values:
        return 0
    return _call(["LPUSH", key, *values])


def rpush(key: str, *values: str) -> int:
    if not values:
        return 0
    return _call(["RPUSH", key, *values])


def lrange(key: str, start: int = 0, stop: int = -1) -> list[str]:
    return _call(["LRANGE", key, start, stop]) or []


def ltrim(key: str, start: int, stop: int) -> None:
    _call(["LTRIM", key, start, stop])


def llen(key: str) -> int:
    return _call(["LLEN", key]) or 0


# ----- Keys -----

def exists(key: str) -> bool:
    return bool(_call(["EXISTS", key]))


def keys(pattern: str = "*") -> list[str]:
    """Avoid in production — O(n). Fine for migrations / one-offs."""
    return _call(["KEYS", pattern]) or []

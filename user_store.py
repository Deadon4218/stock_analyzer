"""
Per-user data — watchlist, chat_id, etc.
Backed by Upstash Redis.

Redis layout:
  user:{chat_id}   — JSON string {watchlist: [...], chat_id: int, ...}
  users:index      — Set of all known chat_id strings (for get_all_users)
"""
import redis_store as r

_K_USER = "user:{chat_id}"
_K_INDEX = "users:index"


def _user_key(chat_id: int) -> str:
    return _K_USER.format(chat_id=chat_id)


def get_user(chat_id: int) -> dict:
    data = r.get_json(_user_key(chat_id))
    if data is None:
        return {"watchlist": [], "chat_id": chat_id}
    return data


def get_all_users() -> list[dict]:
    ids = r.smembers(_K_INDEX)
    out = []
    for cid in ids:
        try:
            chat_id = int(cid)
        except ValueError:
            continue
        out.append(get_user(chat_id))
    return out


def _save_user(chat_id: int, data: dict):
    r.set_json(_user_key(chat_id), data)
    r.sadd(_K_INDEX, str(chat_id))


def add_ticker(chat_id: int, ticker: str) -> bool:
    user = get_user(chat_id)
    ticker = ticker.upper().strip()
    if ticker in user["watchlist"]:
        return False
    user["watchlist"].append(ticker)
    _save_user(chat_id, user)
    return True


def remove_ticker(chat_id: int, ticker: str) -> bool:
    user = get_user(chat_id)
    ticker = ticker.upper().strip()
    if ticker not in user["watchlist"]:
        return False
    user["watchlist"].remove(ticker)
    _save_user(chat_id, user)
    return True


def get_watchlist(chat_id: int) -> list[str]:
    return get_user(chat_id).get("watchlist", [])

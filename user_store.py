import json
import os
from pathlib import Path

DATA_FILE = Path(os.environ.get("USER_DATA_PATH", "data/users.json"))


def _load() -> dict:
    if not DATA_FILE.exists():
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def _save(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user(chat_id: int) -> dict:
    data = _load()
    return data.get(str(chat_id), {"watchlist": [], "chat_id": chat_id})


def get_all_users() -> list[dict]:
    data = _load()
    return list(data.values())


def add_ticker(chat_id: int, ticker: str) -> bool:
    data = _load()
    key = str(chat_id)
    if key not in data:
        data[key] = {"watchlist": [], "chat_id": chat_id}

    ticker = ticker.upper().strip()
    if ticker in data[key]["watchlist"]:
        return False

    data[key]["watchlist"].append(ticker)
    _save(data)
    return True


def remove_ticker(chat_id: int, ticker: str) -> bool:
    data = _load()
    key = str(chat_id)
    if key not in data:
        return False

    ticker = ticker.upper().strip()
    if ticker not in data[key]["watchlist"]:
        return False

    data[key]["watchlist"].remove(ticker)
    _save(data)
    return True


def get_watchlist(chat_id: int) -> list[str]:
    user = get_user(chat_id)
    return user.get("watchlist", [])

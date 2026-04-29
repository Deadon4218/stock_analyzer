import os
import time
import random
import base64
import requests

DISCORD_API = "https://discord.com/api/v10"


def _headers():
    token = os.environ["DISCORD_USER_TOKEN"]
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }


def fetch_messages(channel_id: str, limit: int = 75, _retries: int = 0) -> list[dict]:
    """
    Fetch messages from a Discord channel.
    Returns list of dicts with: author, content, timestamp, id, images
    """
    MAX_RETRIES = 3
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    params = {"limit": min(limit, 100)}

    resp = requests.get(url, headers=_headers(), params=params, timeout=10)

    if resp.status_code == 401:
        raise ValueError("❌ Invalid token — check DISCORD_USER_TOKEN")
    if resp.status_code == 403:
        raise ValueError("❌ No access to this channel with current account")
    if resp.status_code == 429:
        if _retries >= MAX_RETRIES:
            raise ValueError("❌ Rate limit persists — retrying next cycle")
        retry_after = resp.json().get("retry_after", 60)
        print(f"⏳ Rate limit — waiting {retry_after}s (attempt {_retries + 1}/{MAX_RETRIES})")
        time.sleep(float(retry_after) + 1)
        return fetch_messages(channel_id, limit, _retries + 1)

    resp.raise_for_status()
    raw = resp.json()

    messages = []
    for m in raw:
        # Extract image URLs from attachments and embeds
        image_urls = []
        for att in m.get("attachments", []):
            if att.get("content_type", "").startswith("image/"):
                image_urls.append(att["url"])
        for embed in m.get("embeds", []):
            if embed.get("image", {}).get("url"):
                image_urls.append(embed["image"]["url"])
            if embed.get("thumbnail", {}).get("url"):
                image_urls.append(embed["thumbnail"]["url"])

        messages.append({
            "author": m.get("author", {}).get("username", "unknown"),
            "content": m.get("content", "").strip(),
            "timestamp": m.get("timestamp", ""),
            "id": m.get("id", ""),
            "image_urls": image_urls,
        })

    # Return oldest first
    return list(reversed(messages))


def download_image_as_base64(url: str, timeout: int = 10) -> str | None:
    """
    Download an image from URL and return as base64 string.
    Returns None on failure.
    """
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "image/png")
        if not content_type.startswith("image/"):
            return None

        # Limit to 10MB
        data = resp.content
        if len(data) > 10 * 1024 * 1024:
            print(f"   ⚠️  Image too large ({len(data) / 1024 / 1024:.1f}MB), skipping")
            return None

        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:{content_type};base64,{b64}"

    except Exception as e:
        print(f"   ⚠️  Failed to download image: {e}")
        return None


def search_messages_for_ticker(messages: list[dict], ticker: str) -> list[dict]:
    """Filter messages containing a specific ticker symbol"""
    ticker_upper = ticker.upper()
    return [
        m for m in messages
        if ticker_upper in m["content"].upper()
        or f"${ticker_upper}" in m["content"].upper()
    ]


def random_sleep():
    """
    Wait a random interval between POLL_MIN and POLL_MAX minutes.
    Prints countdown every minute.
    """
    min_min = float(os.environ.get("POLL_MIN_MINUTES", 5))
    max_min = float(os.environ.get("POLL_MAX_MINUTES", 15))
    wait_seconds = random.uniform(min_min * 60, max_min * 60)
    wait_minutes = wait_seconds / 60

    print(f"\n⏱  Next poll in {wait_minutes:.1f} minutes")

    elapsed = 0
    interval = 60
    while elapsed < wait_seconds:
        remaining = wait_seconds - elapsed
        mins_left = remaining / 60
        print(f"   ⏳ {mins_left:.1f} min remaining...", end="\r")
        sleep_now = min(interval, remaining)
        time.sleep(sleep_now)
        elapsed += sleep_now

    print()

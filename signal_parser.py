import os
import json
from dataclasses import dataclass, field
from typing import Optional
from groq_client import get_groq


@dataclass
class StockSignal:
    ticker: str
    entry_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    breakout_level: Optional[float] = None
    direction: str = "long"
    raw_message: str = ""
    author: str = ""
    timestamp: str = ""
    image_urls: list = field(default_factory=list)

    def risk_reward_ratio(self) -> Optional[float]:
        if self.entry_price is None or self.take_profit is None or self.stop_loss is None:
            return None
        reward = abs(self.take_profit - self.entry_price)
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return None
        return round(reward / risk, 2)

    def __str__(self):
        rr = self.risk_reward_ratio()
        rr_str = f"R:R {rr:.2f}" if rr else "R:R N/A"
        parts = [f"📊 {self.ticker}"]
        if self.entry_price:
            parts.append(f"Entry: {self.entry_price}")
        if self.breakout_level:
            parts.append(f"Breakout: {self.breakout_level}")
        if self.take_profit:
            parts.append(f"TP: {self.take_profit}")
        if self.stop_loss:
            parts.append(f"SL: {self.stop_loss}")
        parts.append(rr_str)
        if self.image_urls:
            parts.append(f"📸 {len(self.image_urls)} chart(s)")
        return " | ".join(parts)


EXTRACTION_PROMPT = """You are a stock signal extraction engine. You read Discord messages
(often in Hebrew) from a stock trading channel and extract structured data.

Return ONLY valid JSON, no extra text. Extract:
{
  "signals": [
    {
      "ticker": "SYMBOL",
      "entry_price": <number or null>,
      "take_profit": <number or null>,
      "stop_loss": <number or null>,
      "breakout_level": <number or null>,
      "direction": "long" or "short",
      "context": "<brief English summary of what the message says about this stock>"
    }
  ]
}

Rules:
- ticker: US stock symbol in CAPS (AMZN, TSLA, SYNA, etc.)
- entry_price: the suggested entry/buy price. IMPORTANT: if a breakout price is mentioned
  (פריצה מעל X / פריצה מ-X), that IS the entry price — set BOTH entry_price AND breakout_level to that number.
  Also look for: כניסה, מחיר כניסה, כניסה ב-, נכנסים ב-, להיכנס ב-
- take_profit: target/exit price. Look for: יעד, טייק פרופיט, TP, מטרה, יעד ראשון, יעד שני
- stop_loss: stop loss level. Look for: סטופ, סטופ לוס, SL, סטופ מתחת ל-, סטופ מקסימום
- breakout_level: the breakout price (פריצה מעל/מ-)
- direction: "long" (default) or "short". Look for: שורט=short, לונג=long
- If a value is not mentioned, set it to null
- Additional Hebrew terms: ממוצע=moving average, תמיכה=support, התנגדות=resistance,
  דיווח=earnings report, נר פטיש=hammer candle, דוג'י=doji, גאפ=gap
- If no stock signal is found in the message, return {"signals": []}
- Multiple tickers in one message = multiple items in the array
"""


def extract_signals_from_messages(messages: list[dict]) -> list[StockSignal]:
    """
    Send a batch of Discord messages to Groq LLM for signal extraction.
    Returns list of StockSignal objects.
    """
    # Build context from messages
    msg_texts = []
    msg_lookup = {}  # map message content to metadata

    for m in messages:
        content = m.get("content", "").strip()
        if not content or len(content) < 3:
            continue

        msg_texts.append(f"[{m['author']}]: {content}")
        msg_lookup[content] = m

    if not msg_texts:
        return []

    # Send to Groq for extraction
    combined = "\n".join(msg_texts[-15:])

    import time as _time
    import re as _re

    for _attempt in range(3):
        try:
            client = get_groq()
            response = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {"role": "user", "content": f"Extract signals from these messages:\n\n{combined}"},
                ],
                temperature=0.1,
                max_tokens=4000,
                timeout=60,
            )

            raw = response.choices[0].message.content.strip()
            if "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                signal_pattern = _re.compile(r'\{[^{}]*"ticker"\s*:\s*"[A-Z]{1,5}"[^{}]*\}', _re.DOTALL)
                found = signal_pattern.findall(raw)
                if found:
                    repaired_signals = []
                    for chunk in found:
                        try:
                            repaired_signals.append(json.loads(chunk))
                        except json.JSONDecodeError:
                            pass
                    data = {"signals": repaired_signals}
                else:
                    raise
            break

        except Exception as e:
            err_str = str(e)
            if "429" in err_str and _attempt < 2:
                wait = float(_re.search(r"try again in (\d+\.?\d*)", err_str).group(1)) if _re.search(r"try again in (\d+\.?\d*)", err_str) else 10
                print(f"   ⏳ Rate limited — waiting {wait:.1f}s (attempt {_attempt + 1}/3)")
                _time.sleep(wait + 1)
                continue
            print(f"   ⚠️  LLM extraction failed: {e}")
        return []

    # Convert to StockSignal objects
    signals = []
    seen_tickers = set()

    for item in data.get("signals", []):
        ticker = item.get("ticker", "").upper().strip()
        if not ticker or len(ticker) > 5 or ticker in seen_tickers:
            continue

        seen_tickers.add(ticker)

        # Find the original message for metadata
        source_msg = None
        for m in reversed(messages):
            if ticker in m.get("content", "").upper():
                source_msg = m
                break

        entry = _safe_float(item.get("entry_price"))
        breakout = _safe_float(item.get("breakout_level"))

        # If no entry but breakout exists, use breakout as entry
        if entry is None and breakout is not None:
            entry = breakout

        direction = item.get("direction", "long")
        if direction not in ("long", "short"):
            direction = "long"

        signals.append(StockSignal(
            ticker=ticker,
            entry_price=entry,
            take_profit=_safe_float(item.get("take_profit")),
            stop_loss=_safe_float(item.get("stop_loss")),
            breakout_level=breakout,
            direction=direction,
            raw_message=source_msg.get("content", "") if source_msg else "",
            author=source_msg.get("author", "") if source_msg else "",
            timestamp=source_msg.get("timestamp", "") if source_msg else "",
            image_urls=source_msg.get("image_urls", []) if source_msg else [],
        ))

    return signals


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

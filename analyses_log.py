"""
Append-only history of every analysis we run.
data/analyses.jsonl — one JSON line per analysis.
Used for: backtesting strategies, agent accuracy tracking, dashboard.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(os.environ.get("ANALYSES_LOG_PATH", "data/analyses.jsonl"))


def _agent_to_dict(v) -> dict:
    return {
        "name": v.agent_name,
        "type": v.agent_type,
        "stance": v.stance,
        "score": round(v.score, 3),
        "confidence": round(v.confidence, 3),
        "reasoning": v.reasoning[:400],
    }


def make_id(ticker: str, ts: str) -> str:
    """Stable ID = ticker + iso timestamp (truncated to seconds)."""
    return f"{ticker}:{ts.replace(':', '').replace('-', '')[:15]}"


def log_analysis(result, source: str):
    """
    Append a structured record for an AnalysisResult.
    source: 'broadcast' | 'personal' | 'manual'
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = {
        "id": make_id(result.ticker, ts),
        "ts": ts,
        "ticker": result.ticker,
        "source": source,
        "verdict": "ENTRY" if result.should_enter else "SKIP",
        "bull_prob": round(result.bull_probability, 4),
        "bear_prob": round(result.bear_probability, 4),
        "ratio": round(result.probability_ratio(), 3) if result.bear_probability > 0 else None,
        "agents": [_agent_to_dict(v) for v in result.bull_verdicts + result.bear_verdicts],
        "outcome": None,
    }

    if result.price_levels:
        pl = result.price_levels
        record["entry"] = pl.entry
        record["take_profit"] = pl.take_profit
        record["stop_loss"] = pl.stop_loss
        record["rr_ratio"] = pl.rr_ratio

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_all() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_all(records: list[dict]):
    """Rewrite the file — used by outcome_tracker to update outcomes in place."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

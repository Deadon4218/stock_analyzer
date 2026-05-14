"""
Append-only history of every analysis we run.
Backed by Upstash Redis list — one JSON entry per record.
Used for: backtesting strategies, agent accuracy tracking, dashboard.

Redis layout:
  analyses   — List of JSON strings, oldest at head, newest at tail.
               Capped at MAX_RECORDS via LTRIM to fit Upstash storage.
"""
import json
from datetime import datetime, timezone

import redis_store as r

_K_LOG = "analyses"
MAX_RECORDS = 10_000  # Upstash free tier: 256MB, ~1-2KB per record → safe


def _agent_to_dict(v) -> dict:
    return {
        "name": v.agent_name,
        "type": v.agent_type,
        "stance": v.stance,
        "p_up": round(v.p_up, 3),
        "score": round(v.score, 3),
        "confidence": round(v.confidence, 3),
        "reasoning": v.reasoning[:400],
        "key_points": v.key_points[:6],
    }


def make_id(ticker: str, ts: str) -> str:
    """Stable ID = ticker + iso timestamp (truncated to seconds)."""
    return f"{ticker}:{ts.replace(':', '').replace('-', '')[:15]}"


def log_analysis(result, source: str):
    """
    Append a structured record for an AnalysisResult.
    source: 'broadcast' | 'personal' | 'manual'
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = {
        "id": make_id(result.ticker, ts),
        "ts": ts,
        "ticker": result.ticker,
        "direction": result.direction,
        "analysis_mode": result.analysis_mode,
        "source": source,
        "verdict": "ENTRY" if result.should_enter else "SKIP",
        "score_contract": "directional_p_up",
        "bull_prob": round(result.bull_probability, 4),
        "bear_prob": round(result.bear_probability, 4),
        "ratio": round(result.probability_ratio(), 3) if result.bear_probability > 0 else None,
        "agents": [_agent_to_dict(v) for v in result.bull_verdicts + result.bear_verdicts],
        "features": result.features,
        "agent_weights": result.agent_weights,
        "entry_block_reasons": result.entry_block_reasons,
        "outcome": None,
    }

    for agent in result.bull_verdicts + result.bear_verdicts:
        if agent.agent_type == "classic_technical":
            record["classic_p_up"] = round(agent.p_up, 4)
            record["classic_confidence"] = round(agent.confidence, 4)
            break

    if result.price_levels:
        pl = result.price_levels
        record["entry"] = pl.entry
        record["take_profit"] = pl.take_profit
        record["stop_loss"] = pl.stop_loss
        record["rr_ratio"] = pl.rr_ratio

    r.rpush(_K_LOG, json.dumps(record, separators=(",", ":")))

    # Cap the list size — keep newest MAX_RECORDS, drop the rest from the head.
    length = r.llen(_K_LOG)
    if length > MAX_RECORDS:
        r.ltrim(_K_LOG, length - MAX_RECORDS, -1)


def read_all() -> list[dict]:
    raw = r.lrange(_K_LOG, 0, -1)
    out = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_all(records: list[dict]):
    """Rewrite the log — used by outcome_tracker to update outcomes in place.
    Atomicity caveat: Upstash REST is per-command, so a delete-then-rpush has
    a brief window where the list is empty. Acceptable for our usage."""
    r.delete(_K_LOG)
    if records:
        # rpush in chunks of 500 to stay well under any payload size limit
        for i in range(0, len(records), 500):
            chunk = records[i : i + 500]
            r.rpush(_K_LOG, *[json.dumps(rec, separators=(",", ":")) for rec in chunk])

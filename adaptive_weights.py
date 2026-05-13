import os
from collections import defaultdict

from analyses_log import read_all


MIN_WEIGHT_SAMPLES = int(os.environ.get("MIN_WEIGHT_SAMPLES", 20))
WEIGHT_MIN = float(os.environ.get("AGENT_WEIGHT_MIN", 0.6))
WEIGHT_MAX = float(os.environ.get("AGENT_WEIGHT_MAX", 1.4))


def _is_resolved(record: dict) -> bool:
    return record.get("outcome") in ("tp_hit", "sl_hit", "expired")


def _actual_up_outcome(record: dict) -> float:
    if record.get("direction", "long") == "short":
        return 1.0 if record.get("outcome") == "sl_hit" else 0.0
    return 1.0 if record.get("outcome") == "tp_hit" else 0.0


def _agent_p_up(record: dict, agent: dict) -> float | None:
    if "p_up" in agent:
        return float(agent["p_up"])
    score = agent.get("score")
    if score is None:
        return None
    if record.get("score_contract") == "directional_p_up":
        return float(score)
    if agent.get("stance") == "bear":
        return 1 - float(score)
    return float(score)


def get_agent_weights(records: list[dict] | None = None) -> dict[str, float]:
    """
    Compute conservative performance weights from resolved outcomes.
    Weights stay at 1.0 until each agent has enough samples.
    """
    records = records if records is not None else read_all()
    buckets: dict[str, list[float]] = defaultdict(list)

    for record in records:
        if not _is_resolved(record):
            continue
        actual = _actual_up_outcome(record)
        for agent in record.get("agents", []):
            p_up = _agent_p_up(record, agent)
            if p_up is None:
                continue
            p_up = max(0.0, min(1.0, p_up))
            buckets[agent.get("name", "unknown")].append((p_up - actual) ** 2)

    weights: dict[str, float] = {}
    for name, briers in buckets.items():
        n = len(briers)
        if n < MIN_WEIGHT_SAMPLES:
            continue
        avg_brier = sum(briers) / n
        raw = 1 + (0.25 - avg_brier) * 2
        raw = max(WEIGHT_MIN, min(WEIGHT_MAX, raw))
        shrink = n / (n + MIN_WEIGHT_SAMPLES)
        weights[name] = round(1 + (raw - 1) * shrink, 3)

    return weights

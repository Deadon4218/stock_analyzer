from agents import AgentVerdict
from features import extract_features
from signal_parser import StockSignal
from stock_data import StockData


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _add(points: list[str], label: str, delta: float):
    sign = "+" if delta >= 0 else ""
    points.append(f"{label} ({sign}{delta:.0f})")


def run_classic_agent(
    signal: StockSignal,
    data: StockData,
    price_levels,
    chart_analyses: list[dict],
    features: dict | None = None,
) -> AgentVerdict:
    """
    Deterministic technical agent.
    Returns p_up like the LLM agents, but uses transparent technical rules only.
    """
    f = features or extract_features(signal, data, price_levels, chart_analyses)
    if data.error or data.current_price <= 0:
        return AgentVerdict(
            agent_name="Classic Technical",
            agent_type="classic_technical",
            stance="bull",
            p_up=0.5,
            confidence=0.05,
            reasoning=f"Classic rules skipped: {data.error or 'missing market data'}",
            key_points=[],
        )

    score = 50.0
    confidence = 0.55
    points: list[str] = []

    if f["above_50ma"]:
        score += 8
        _add(points, "price above MA50", 8)
    else:
        score -= 8
        _add(points, "price below MA50", -8)

    if f["above_200ma"]:
        score += 12
        _add(points, "price above MA200", 12)
    else:
        score -= 12
        _add(points, "price below MA200", -12)

    change_20d = f.get("change_20d_pct")
    if change_20d is not None:
        if change_20d > 8:
            score += 10
            _add(points, "strong 20d momentum", 10)
        elif change_20d > 1:
            score += 5
            _add(points, "positive 20d momentum", 5)
        elif change_20d < -8:
            score -= 10
            _add(points, "weak 20d momentum", -10)
        elif change_20d < -1:
            score -= 5
            _add(points, "negative 20d momentum", -5)

    change_5d = f.get("change_5d_pct")
    if change_5d is not None:
        if change_5d > 4:
            score += 5
            _add(points, "positive 5d follow-through", 5)
        elif change_5d < -4:
            score -= 5
            _add(points, "negative 5d follow-through", -5)

    rsi = f.get("rsi_14")
    if rsi is not None:
        if 45 <= rsi <= 65:
            score += 6
            _add(points, "RSI in constructive zone", 6)
        elif 65 < rsi <= 72:
            score -= 3
            _add(points, "RSI slightly extended", -3)
        elif rsi > 72:
            score -= 12
            _add(points, "RSI overbought", -12)
        elif rsi < 30:
            score += 3
            confidence -= 0.05
            _add(points, "RSI oversold bounce potential", 3)

    vol = f.get("volume_spike")
    if vol is not None:
        if vol >= 1.5 and (change_5d or 0) > 0:
            score += 7
            _add(points, "volume confirms upside", 7)
        elif vol < 0.55:
            score -= 4
            _add(points, "weak relative volume", -4)

    pct_high = f.get("pct_from_52w_high")
    if pct_high is not None:
        if pct_high > -10:
            score += 5
            _add(points, "near 52w high", 5)
        elif pct_high < -40:
            score -= 8
            _add(points, "far below 52w high", -8)
        elif pct_high < -25:
            score -= 4
            _add(points, "well below 52w high", -4)

    trends = f.get("chart_trends") or []
    if "uptrend" in trends:
        score += 8
        _add(points, "chart trend up", 8)
    if "downtrend" in trends:
        score -= 8
        _add(points, "chart trend down", -8)

    patterns = " ".join(f.get("chart_patterns") or [])
    if "breakout" in patterns:
        score += 6
        _add(points, "breakout pattern", 6)
    if "breakdown" in patterns or "lower high" in patterns:
        score -= 7
        _add(points, "bearish pattern risk", -7)

    rr = f.get("rr_ratio")
    if rr is not None:
        if rr >= 2:
            confidence += 0.08
            points.append("R:R is acceptable")
        elif rr < 1.5:
            confidence -= 0.25
            points.append("R:R is below minimum quality")

    risk_atr = f.get("risk_atr_ratio")
    if risk_atr is not None:
        if risk_atr < 0.8:
            confidence -= 0.2
            points.append("stop is tighter than normal ATR noise")
        elif risk_atr > 3:
            confidence -= 0.08
            points.append("stop is wide relative to ATR")
        else:
            confidence += 0.05
            points.append("stop distance is reasonable vs ATR")

    score = max(5.0, min(95.0, score))
    confidence += abs(score - 50) / 50 * 0.2
    if f.get("chart_count", 0) > 0:
        confidence += 0.05
    confidence = _clamp(confidence, 0.2, 0.92)

    p_up = round(score / 100, 4)
    direction = signal.direction if signal.direction in ("long", "short") else "long"
    trade_fit = p_up if direction == "long" else 1 - p_up
    if trade_fit >= 0.67:
        summary = f"Classic rules support the {direction.upper()} setup."
    elif trade_fit <= 0.45:
        summary = f"Classic rules do not support the {direction.upper()} setup."
    else:
        summary = f"Classic rules are mixed for the {direction.upper()} setup."

    return AgentVerdict(
        agent_name="Classic Technical",
        agent_type="classic_technical",
        stance="bull",
        p_up=p_up,
        confidence=round(confidence, 4),
        reasoning=f"{summary} Deterministic score is {score:.0f}/100.",
        key_points=points[:6],
    )

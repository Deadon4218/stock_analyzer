from typing import Any

from signal_parser import StockSignal
from stock_data import StockData


def _pct(current: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return round((current - prev) / prev * 100, 3)


def _nearest_level_distance(entry: float | None, levels: list[float], side: str) -> float | None:
    if entry is None or entry == 0:
        return None
    if side == "below":
        candidates = [level for level in levels if level < entry]
        level = max(candidates) if candidates else None
    else:
        candidates = [level for level in levels if level > entry]
        level = min(candidates) if candidates else None
    if level is None:
        return None
    return round((level - entry) / entry * 100, 3)


def extract_features(
    signal: StockSignal,
    data: StockData,
    price_levels: Any,
    chart_analyses: list[dict],
) -> dict:
    """Build stable numeric/context features for backtesting and non-LLM agents."""
    entry = getattr(price_levels, "entry", None)
    tp = getattr(price_levels, "take_profit", None)
    sl = getattr(price_levels, "stop_loss", None)
    rr = getattr(price_levels, "rr_ratio", None)

    risk = abs(entry - sl) if entry is not None and sl is not None else None
    reward = abs(tp - entry) if entry is not None and tp is not None else None
    atr = data.atr_14 or None

    supports: list[float] = []
    resistances: list[float] = []
    chart_trends: list[str] = []
    chart_patterns: list[str] = []
    for chart in chart_analyses:
        supports.extend(chart.get("support_levels", []) or [])
        resistances.extend(chart.get("resistance_levels", []) or [])
        if chart.get("trend"):
            chart_trends.append(str(chart["trend"]).lower())
        chart_patterns.extend(str(p).lower() for p in chart.get("patterns", []) or [])

    return {
        "direction": signal.direction,
        "analysis_mode": signal.analysis_mode,
        "has_signal_entry": signal.entry_price is not None,
        "has_signal_take_profit": signal.take_profit is not None,
        "has_signal_stop_loss": signal.stop_loss is not None,
        "entry": entry,
        "take_profit": tp,
        "stop_loss": sl,
        "rr_ratio": rr,
        "risk_pct": round(risk / entry * 100, 3) if risk is not None and entry else None,
        "reward_pct": round(reward / entry * 100, 3) if reward is not None and entry else None,
        "risk_atr_ratio": round(risk / atr, 3) if risk is not None and atr else None,
        "reward_atr_ratio": round(reward / atr, 3) if reward is not None and atr else None,
        "entry_gap_pct": round((entry - data.current_price) / data.current_price * 100, 3)
        if entry is not None and data.current_price
        else None,
        "current_price": data.current_price,
        "change_1d_pct": _pct(data.current_price, data.price_1d_ago),
        "change_5d_pct": _pct(data.current_price, data.price_5d_ago),
        "change_20d_pct": _pct(data.current_price, data.price_20d_ago),
        "volume_spike": data.volume_spike(),
        "rsi_14": data.rsi_14,
        "atr_14": data.atr_14,
        "above_50ma": data.above_50ma,
        "above_200ma": data.above_200ma,
        "pct_from_52w_high": data.pct_from_high(),
        "sector": data.sector,
        "chart_count": len(chart_analyses),
        "chart_trends": chart_trends,
        "chart_patterns": chart_patterns,
        "nearest_support_pct": _nearest_level_distance(entry, supports, "below"),
        "nearest_resistance_pct": _nearest_level_distance(entry, resistances, "above"),
        "price_level_source": getattr(price_levels, "source", ""),
    }

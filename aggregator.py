from dataclasses import dataclass
from typing import Optional
from agents import AgentVerdict
from signal_parser import StockSignal
from stock_data import StockData


THRESHOLD = 0.67
MIN_RR = 2.0


@dataclass
class PriceLevels:
    entry: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    rr_ratio: Optional[float] = None
    source: str = ""

    def __str__(self):
        parts = []
        if self.entry:
            parts.append(f"Entry: ${self.entry:.2f}")
        if self.take_profit:
            parts.append(f"TP: ${self.take_profit:.2f}")
        if self.stop_loss:
            parts.append(f"SL: ${self.stop_loss:.2f}")
        if self.rr_ratio:
            parts.append(f"R:R {self.rr_ratio:.2f}")
        return " | ".join(parts) if parts else "No price levels"


def calculate_price_levels(
    signal: StockSignal,
    data: StockData,
    chart_analyses: list[dict],
) -> PriceLevels:
    entry = signal.entry_price
    tp = signal.take_profit
    sl = signal.stop_loss
    sources = []

    # --- Entry ---
    if entry is None and signal.breakout_level is not None:
        entry = signal.breakout_level
        sources.append("entry=breakout")
    if entry is None and data.current_price > 0:
        entry = round(data.current_price, 2)
        sources.append("entry=current_price")

    if entry is None:
        return PriceLevels(source="insufficient data")

    # --- Stop Loss ---
    if sl is None and data.atr_14:
        sl = round(entry - 1.5 * data.atr_14, 2)
        sources.append("SL=1.5×ATR")

    # Try chart support levels as SL
    if sl is None:
        for chart in chart_analyses:
            supports = chart.get("support_levels", [])
            below_entry = [s for s in supports if s < entry]
            if below_entry:
                sl = round(max(below_entry), 2)
                sources.append("SL=chart_support")
                break

    if sl is None:
        sl = round(entry * 0.95, 2)
        sources.append("SL=5%_default")

    # --- Take Profit ---
    if tp is None:
        # Try chart resistance levels
        for chart in chart_analyses:
            resistances = chart.get("resistance_levels", [])
            above_entry = [r for r in resistances if r > entry]
            if above_entry:
                tp = round(min(above_entry), 2)
                sources.append("TP=chart_resistance")
                break

    if tp is None:
        risk = abs(entry - sl)
        tp = round(entry + risk * MIN_RR, 2)
        sources.append(f"TP={MIN_RR:.0f}×risk")

    # --- R:R ---
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else None

    return PriceLevels(
        entry=entry,
        take_profit=tp,
        stop_loss=sl,
        rr_ratio=rr,
        source=", ".join(sources),
    )


@dataclass
class AnalysisResult:
    ticker: str
    bull_probability: float
    bear_probability: float
    should_enter: bool
    bull_verdicts: list[AgentVerdict]
    bear_verdicts: list[AgentVerdict]
    top_bull_reason: str
    top_bear_reason: str
    price_levels: Optional[PriceLevels] = None

    def probability_ratio(self) -> float:
        if self.bear_probability == 0:
            return float("inf")
        return round(self.bull_probability / self.bear_probability, 3)

    def formatted_report(self) -> str:
        lines = [
            "=" * 60,
            f"📊 Analysis: {self.ticker}",
            "=" * 60,
            f"🟢 Bull probability:  {self.bull_probability:.1%}",
            f"🔴 Bear probability:  {self.bear_probability:.1%}",
            f"📐 Ratio:             {self.probability_ratio():.2f}x",
            f"🎯 Required threshold: {THRESHOLD:.0%}",
        ]

        if self.price_levels and self.price_levels.entry:
            pl = self.price_levels
            lines.append("")
            lines.append("── Price Levels ──")
            lines.append(f"  💰 Entry:       ${pl.entry:.2f}")
            if pl.take_profit:
                lines.append(f"  🎯 Take Profit: ${pl.take_profit:.2f}")
            if pl.stop_loss:
                lines.append(f"  🛑 Stop Loss:   ${pl.stop_loss:.2f}")
            if pl.rr_ratio:
                lines.append(f"  📐 R:R Ratio:   {pl.rr_ratio:.2f}")
            lines.append(f"  📎 Sources:     {pl.source}")

        lines.append("")
        if self.should_enter:
            lines.append("✅ ENTRY RECOMMENDED!")
        else:
            lines.append("❌ DO NOT ENTER — below 67% threshold")

        lines.append("")
        lines.append("── Bull Agents ──")

        for v in sorted(self.bull_verdicts, key=lambda x: x.score, reverse=True):
            lines.append(f"  🟢 {v.agent_name} [{v.score:.2f}]: {v.reasoning}")
            for pt in v.key_points:
                lines.append(f"     • {pt}")

        lines.append("")
        lines.append("── Bear Agents ──")

        for v in sorted(self.bear_verdicts, key=lambda x: x.score, reverse=True):
            lines.append(f"  🔴 {v.agent_name} [{v.score:.2f}]: {v.reasoning}")
            for pt in v.key_points:
                lines.append(f"     • {pt}")

        lines.append("=" * 60)
        return "\n".join(lines)


def aggregate(
    ticker: str,
    bull_verdicts: list[AgentVerdict],
    bear_verdicts: list[AgentVerdict],
    price_levels: Optional[PriceLevels] = None,
) -> AnalysisResult:
    def weighted_avg(verdicts: list[AgentVerdict]) -> float:
        total_weight = sum(v.confidence for v in verdicts)
        if total_weight == 0:
            return 0.5
        return sum(v.score * v.confidence for v in verdicts) / total_weight

    bull_raw = weighted_avg(bull_verdicts)
    bear_raw = weighted_avg(bear_verdicts)

    total = bull_raw + bear_raw
    if total == 0:
        bull_prob = bear_prob = 0.5
    else:
        bull_prob = round(bull_raw / total, 4)
        bear_prob = round(bear_raw / total, 4)

    should_enter = bull_prob >= THRESHOLD

    top_bull = max(bull_verdicts, key=lambda v: v.score * v.confidence)
    top_bear = max(bear_verdicts, key=lambda v: v.score * v.confidence)

    return AnalysisResult(
        ticker=ticker,
        bull_probability=bull_prob,
        bear_probability=bear_prob,
        should_enter=should_enter,
        bull_verdicts=bull_verdicts,
        bear_verdicts=bear_verdicts,
        top_bull_reason=top_bull.reasoning,
        top_bear_reason=top_bear.reasoning,
        price_levels=price_levels,
    )

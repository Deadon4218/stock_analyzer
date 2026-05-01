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
    failed_count: int = 0
    total_count: int = 10

    def reliability(self) -> float:
        """Fraction of agents that produced real verdicts (0.0–1.0)."""
        if self.total_count == 0:
            return 0.0
        return round(1 - self.failed_count / self.total_count, 2)

    def is_unreliable(self) -> bool:
        """True when fewer than half the agents succeeded — verdict shouldn't be trusted."""
        return self.reliability() < 0.5

    def probability_ratio(self) -> float:
        # Returns a JSON-safe value. When bear=0 (extremely rare),
        # we cap at 99.0 instead of inf so reports/serialization work.
        if self.bear_probability == 0:
            return 99.0
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


def _is_failed(v: AgentVerdict) -> bool:
    """An agent verdict is 'failed' if it crashed and returned the neutral fallback."""
    return v.confidence <= 0.1 and v.reasoning.startswith("Analysis error")


def aggregate(
    ticker: str,
    bull_verdicts: list[AgentVerdict],
    bear_verdicts: list[AgentVerdict],
    price_levels: Optional[PriceLevels] = None,
) -> AnalysisResult:
    # Exclude failed agents from probability math so 50/50 fallbacks don't dilute real votes.
    real_bulls = [v for v in bull_verdicts if not _is_failed(v)]
    real_bears = [v for v in bear_verdicts if not _is_failed(v)]
    failed_count = (len(bull_verdicts) - len(real_bulls)) + (len(bear_verdicts) - len(real_bears))
    total_count = len(bull_verdicts) + len(bear_verdicts)

    def weighted_avg(verdicts: list[AgentVerdict]) -> float:
        total_weight = sum(v.confidence for v in verdicts)
        if total_weight == 0:
            return 0.5
        return sum(v.score * v.confidence for v in verdicts) / total_weight

    bull_raw = weighted_avg(real_bulls) if real_bulls else 0.5
    bear_raw = weighted_avg(real_bears) if real_bears else 0.5

    total = bull_raw + bear_raw
    if total == 0:
        bull_prob = bear_prob = 0.5
    else:
        bull_prob = round(bull_raw / total, 4)
        bear_prob = round(bear_raw / total, 4)

    # If too many agents failed, don't recommend ENTRY no matter what the math says.
    reliability = 1 - failed_count / total_count if total_count else 0
    should_enter = bull_prob >= THRESHOLD and reliability >= 0.5

    # Pick top reasons from real verdicts only (fall back to fallbacks if none real)
    top_bull = max(real_bulls or bull_verdicts, key=lambda v: v.score * v.confidence)
    top_bear = max(real_bears or bear_verdicts, key=lambda v: v.score * v.confidence)

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
        failed_count=failed_count,
        total_count=total_count,
    )

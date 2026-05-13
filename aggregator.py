import os
from dataclasses import dataclass, field
from typing import Optional
from agents import AgentVerdict
from signal_parser import StockSignal
from stock_data import StockData


THRESHOLD = 0.67
MIN_RR = 2.0
MIN_ENTRY_RR = float(os.environ.get("MIN_ENTRY_RR", 1.5))


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
    direction = signal.direction if signal.direction in ("long", "short") else "long"
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
        sl = round(entry - 1.5 * data.atr_14, 2) if direction == "long" else round(entry + 1.5 * data.atr_14, 2)
        sources.append("SL=1.5×ATR")

    # Try chart levels as SL.
    if sl is None:
        for chart in chart_analyses:
            if direction == "long":
                levels = [s for s in chart.get("support_levels", []) if s < entry]
                level = max(levels) if levels else None
                source = "SL=chart_support"
            else:
                levels = [r for r in chart.get("resistance_levels", []) if r > entry]
                level = min(levels) if levels else None
                source = "SL=chart_resistance"
            if level is not None:
                sl = round(level, 2)
                sources.append(source)
                break

    if sl is None:
        sl = round(entry * 0.95, 2) if direction == "long" else round(entry * 1.05, 2)
        sources.append("SL=5%_default")

    # --- Take Profit ---
    if tp is None:
        # Try chart target levels.
        for chart in chart_analyses:
            if direction == "long":
                levels = [r for r in chart.get("resistance_levels", []) if r > entry]
                level = min(levels) if levels else None
                source = "TP=chart_resistance"
            else:
                levels = [s for s in chart.get("support_levels", []) if s < entry]
                level = max(levels) if levels else None
                source = "TP=chart_support"
            if level is not None:
                tp = round(level, 2)
                sources.append(source)
                break

    if tp is None:
        risk = abs(entry - sl)
        tp = round(entry + risk * MIN_RR, 2) if direction == "long" else round(entry - risk * MIN_RR, 2)
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
    direction: str
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
    features: dict = field(default_factory=dict)
    agent_weights: dict[str, float] = field(default_factory=dict)
    entry_block_reasons: list[str] = field(default_factory=list)

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
            f"Direction:          {self.direction.upper()}",
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
            lines.append(f"✅ {self.direction.upper()} ENTRY RECOMMENDED!")
        else:
            lines.append("❌ DO NOT ENTER — below 67% threshold")
        for reason in self.entry_block_reasons:
            lines.append(f"   Blocked: {reason}")

        lines.append("")
        lines.append("── Upside / Classic Lenses ──")

        for v in sorted(self.bull_verdicts, key=lambda x: x.score, reverse=True):
            lines.append(f"  🟢 {v.agent_name} [{v.score:.2f}]: {v.reasoning}")
            for pt in v.key_points:
                lines.append(f"     • {pt}")

        lines.append("")
        lines.append("── Risk / Downside Lenses ──")

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
    direction: str = "long",
    features: Optional[dict] = None,
    agent_weights: Optional[dict[str, float]] = None,
) -> AnalysisResult:
    # New contract: every agent (bull-leaning or bear-leaning specialty) returns
    # its own directional p_up. We average ALL of them — the bull/bear split is
    # only kept for evidence-lens diversity, not for any normalization step.
    # This prevents the old "both sides hedge to ~0.5 → normalize to 50/50" trap.
    real_bulls = [v for v in bull_verdicts if not _is_failed(v)]
    real_bears = [v for v in bear_verdicts if not _is_failed(v)]
    real_all = real_bulls + real_bears

    failed_count = (len(bull_verdicts) - len(real_bulls)) + (len(bear_verdicts) - len(real_bears))
    total_count = len(bull_verdicts) + len(bear_verdicts)
    if agent_weights is None:
        try:
            from adaptive_weights import get_agent_weights
            agent_weights = get_agent_weights()
        except Exception:
            agent_weights = {}

    if real_all:
        total_weight = sum(v.confidence * agent_weights.get(v.agent_name, 1.0) for v in real_all)
        if total_weight == 0:
            bull_prob = 0.5
        else:
            bull_prob = round(
                sum(v.p_up * v.confidence * agent_weights.get(v.agent_name, 1.0) for v in real_all) / total_weight,
                4,
            )
    else:
        bull_prob = 0.5

    bear_prob = round(1 - bull_prob, 4)

    # If too many agents failed, don't recommend ENTRY no matter what the math says.
    reliability = 1 - failed_count / total_count if total_count else 0
    direction = direction if direction in ("long", "short") else "long"
    trade_prob = bull_prob if direction == "long" else bear_prob
    entry_block_reasons = []
    if price_levels and price_levels.rr_ratio is not None and price_levels.rr_ratio < MIN_ENTRY_RR:
        entry_block_reasons.append(f"R:R {price_levels.rr_ratio:.2f} is below {MIN_ENTRY_RR:.2f}")
    should_enter = trade_prob >= THRESHOLD and reliability >= 0.5 and not entry_block_reasons

    # Pick the most "informative" verdict from each side: highest confidence,
    # tiebroken by how decisive p_up is (distance from 0.5).
    def informativeness(v: AgentVerdict) -> float:
        return v.confidence * abs(v.p_up - 0.5)

    fallback_verdicts = real_all or bull_verdicts or bear_verdicts
    top_bull = max(real_bulls or bull_verdicts or fallback_verdicts, key=informativeness)
    top_bear = max(real_bears or bear_verdicts or fallback_verdicts, key=informativeness)

    return AnalysisResult(
        ticker=ticker,
        direction=direction,
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
        features=features or {},
        agent_weights=agent_weights,
        entry_block_reasons=entry_block_reasons,
    )

import json
import os
import random
import time
import re
from dataclasses import dataclass
from typing import Literal

from groq_client import get_groq
from signal_parser import StockSignal
from stock_data import StockData

MAX_RETRIES = 4
# 8b is weaker than 70b at producing decisive probabilities, but its daily
# token cap (≈500k+ TPD) is the only one that fits a 15-minute cron schedule.
# 70b's 100k TPD burns out in ~1-2 hours of production traffic. The new p_up
# contract in SYSTEM_BASE does most of the architectural work — even with 8b's
# tendency to hedge, the math no longer collapses to 50/50 the way it used to.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "llama-3.1-8b-instant")
# 8b free tier: 30k TPM. 6 agents × ~2k tokens ≈ 12k, well under the cap with
# 4s spacing. Total cycle time per stock ≈ 30-40s.
AGENT_DELAY_SEC = float(os.environ.get("AGENT_DELAY_SEC", 4))
# Cap exponential backoff on 429s
MAX_BACKOFF_SEC = 90
# Output cap for agent responses (just need score/confidence/reasoning JSON).
AGENT_MAX_TOKENS = 500


@dataclass
class AgentVerdict:
    agent_name: str
    agent_type: str       # strategy category for stats grouping
    stance: Literal["bull", "bear"]   # which evidence lens the agent specialized in
    p_up: float           # 0.0–1.0 — agent's overall probability the stock goes up
    confidence: float     # 0.0–1.0 — agent's confidence in its own p_up
    reasoning: str
    key_points: list[str]

    @property
    def score(self) -> float:
        # Backward-compat alias for code that still reads `.score`
        # (analyses_log.py, sort keys in formatted_report, etc.)
        return self.p_up


# Each agent is a SPECIALIST at a specific evidence lens but its final
# verdict is a directional probability (p_up) for the trade as a whole.
# 6 agents (3 bullish-leaning specialists + 3 bearish-leaning specialists)
# instead of 10 — fits the 70b 12k-TPM budget with reasonable spacing.
BULL_AGENTS = [
    {
        "name": "Momentum & Trend",
        "type": "technical_momentum",
        "prompt": (
            "Your specialty: momentum and trend continuation. "
            "Examine daily/weekly/monthly % changes, MA50/MA200 alignment, "
            "higher-lows pattern, and whether the trend supports a long entry. "
            "Pay attention to chart trend analysis if available."
        ),
    },
    {
        "name": "Structure & Levels",
        "type": "technical_indicators",
        "prompt": (
            "Your specialty: technical structure and key levels. "
            "Examine RSI (overbought/oversold), distance from 52-week high, "
            "support/resistance from chart analysis, and whether the entry "
            "sits above strong support."
        ),
    },
    {
        "name": "Volume & Flow",
        "type": "technical_volume",
        "prompt": (
            "Your specialty: volume confirmation and order flow. "
            "Examine today's volume vs 10-day average, accumulation patterns, "
            "and whether volume confirms the recent price move (or diverges from it)."
        ),
    },
]

BEAR_AGENTS = [
    {
        "name": "Reversal & Overbought",
        "type": "technical_pattern",
        "prompt": (
            "Your specialty: reversal patterns and overbought conditions. "
            "Examine RSI > 70, distance already run from 52-week high, "
            "lower-highs forming, and any volume/price divergence signaling exhaustion."
        ),
    },
    {
        "name": "Macro & Sector",
        "type": "macro_trend",
        "prompt": (
            "Your specialty: macro/sector headwinds and primary trend. "
            "Examine whether the stock is below MA200 (primary downtrend), "
            "sector weakness, and whether the entry is against the long-term trend."
        ),
    },
    {
        "name": "Risk & Stop Distance",
        "type": "risk_management",
        "prompt": (
            "Your specialty: risk/reward realism and stop-loss survival. "
            "Examine the R:R ratio, whether the SL is too tight relative to ATR "
            "(likely to be triggered by normal volatility), and whether the target is "
            "realistic vs current price action."
        ),
    },
]

SYSTEM_BASE = """You are a professional stock analysis agent. You will be given a stock signal,
market data, chart analysis, and recent Discord context. Return ONLY valid JSON in this exact format:

{
  "p_up": <number between 0.0 and 1.0>,
  "confidence": <number between 0.0 and 1.0>,
  "reasoning": "<brief explanation in English, 2-3 sentences>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"]
}

DEFINITIONS:
  p_up = your probability that this stock will move UP and the long trade will succeed
         (reach take-profit before stop-loss) over a typical short-term horizon (1-3 weeks).
  confidence = how confident you are in your p_up given the available data.

CRITICAL RULES:
  1. Your specialty is just a LENS — it tells you WHERE to look, not WHAT to conclude.
     A "bearish reversal specialist" who looks at a clearly bullish chart MUST report
     p_up > 0.5 because that is the honest answer. Reporting p_up < 0.5 on a bullish
     chart just because you are a "bear specialist" is WRONG and will be rejected.
  2. p_up is a DIRECTIONAL probability of the trade succeeding — NOT how strong your
     personal argument is. If the evidence is bearish, return p_up close to 0.0 even
     if you specialize in bullish analysis. If bullish, p_up close to 1.0.
  3. USE THE FULL RANGE. Strong setups deserve 0.75–0.95. Weak setups deserve 0.05–0.25.
     Do NOT default to 0.5 unless the evidence is genuinely balanced.

EXAMPLES (study these carefully):

Example A — A "Reversal & Overbought" bear-specialist sees a strongly bullish chart
(above all MAs, RSI 60, near 52w high in confirmed uptrend, no lower-highs):
  CORRECT: p_up = 0.82. The honest read is bullish even from a bear lens.
  WRONG: p_up = 0.25 just because the role label says "bearish."

Example B — A "Momentum & Trend" bull-specialist sees a strongly bearish chart
(below MA200, RSI 28, multi-month downtrend, breaking support):
  CORRECT: p_up = 0.14. The honest read is bearish even from a bull lens.
  WRONG: p_up = 0.55 just because the role label says "bullish."

Example C — A "Macro & Sector" bear-specialist sees a moderately bullish chart
(above MA50 but slightly below MA200, RSI 55, sector mixed):
  CORRECT: p_up = 0.61. Some bullish elements, some headwinds — honest read leans up.

Example D — A "Volume & Flow" bull-specialist sees a moderately bearish chart
(volume divergence, distribution pattern, RSI rolling over):
  CORRECT: p_up = 0.34. Some bullish elements possible, but flow says weakness.

Example E — Genuinely mixed setup (truly ambiguous):
  CORRECT: p_up around 0.45–0.55 with LOW confidence. Coin-flip is honest here.

KEY TAKEAWAY: The numbers above are illustrative — DO NOT copy them verbatim.
Calibrate your own p_up to the SPECIFIC stock data you receive. Use any value in
[0.0, 1.0] that reflects your honest probability assessment.
"""


def _safe_pct(current: float, prev: float) -> str:
    if prev == 0:
        return "N/A"
    return f"{((current - prev) / prev * 100):.2f}%"


def _build_user_prompt(
    signal: StockSignal,
    data: StockData,
    messages_context: str,
    chart_context: str,
) -> str:
    rr = signal.risk_reward_ratio()
    return f"""
Stock signal to analyze:
- Ticker: {signal.ticker}
- Suggested entry: {signal.entry_price or 'not specified'}
- Take profit: {signal.take_profit or 'not specified'}
- Stop loss: {signal.stop_loss or 'not specified'}
- Breakout level: {signal.breakout_level or 'not specified'}
- R:R ratio: {f'{rr:.2f}' if rr else 'N/A'}
- Original message: {signal.raw_message[:300]}

Market data (yfinance):
- Current price: ${data.current_price:.2f}
- 1-day change: {_safe_pct(data.current_price, data.price_1d_ago)}
- 5-day change: {_safe_pct(data.current_price, data.price_5d_ago)}
- 20-day change: {_safe_pct(data.current_price, data.price_20d_ago)}
- RSI(14): {data.rsi_14 or 'N/A'}
- ATR(14): {data.atr_14 or 'N/A'}
- Volume today: {data.volume_today:,} ({data.volume_spike():.1f}x average)
- Above MA50: {'yes' if data.above_50ma else 'no'}
- Above MA200: {'yes' if data.above_200ma else 'no'}
- 52w High: ${data.high_52w:.2f} ({data.pct_from_high():.1f}% from it)
- 52w Low: ${data.low_52w:.2f}
- Sector: {data.sector or 'unknown'}

Chart analysis (from TradingView screenshots):
{chart_context}

Recent Discord messages about this stock:
{messages_context[:800] if messages_context else 'No additional messages'}
"""


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _repair_agent_json(raw: str) -> dict:
    # Accept either "p_up" (new) or "score" (legacy / model occasionally regresses).
    p_up = re.search(r'"p_up"\s*:\s*([\d.]+)', raw) or re.search(r'"score"\s*:\s*([\d.]+)', raw)
    confidence = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
    reasoning = re.search(r'"reasoning"\s*:\s*"([^"]*)', raw)
    if p_up:
        return {
            "p_up": float(p_up.group(1)),
            "confidence": float(confidence.group(1)) if confidence else 0.5,
            "reasoning": reasoning.group(1) if reasoning else "Partial response recovered",
            "key_points": [],
        }
    raise json.JSONDecodeError("Cannot repair", raw, 0)


def _backoff_wait(attempt: int, err_str: str) -> float:
    """Compute wait time on retry: prefer Groq's hint, otherwise exponential + jitter."""
    m = re.search(r"try again in (\d+\.?\d*)", err_str)
    if m:
        return min(float(m.group(1)) + 1, MAX_BACKOFF_SEC)
    # Exponential: 4, 8, 16, 32... with ±20% jitter
    base = min(2 ** (attempt + 2), MAX_BACKOFF_SEC)
    return base * random.uniform(0.8, 1.2)


def run_agent(
    agent_config: dict,
    stance: Literal["bull", "bear"],
    signal: StockSignal,
    data: StockData,
    messages_context: str,
    chart_context: str,
) -> AgentVerdict:
    system_prompt = SYSTEM_BASE + f"\n\nYour specific role: {agent_config['prompt']}"
    user_prompt = _build_user_prompt(signal, data, messages_context, chart_context)

    for attempt in range(MAX_RETRIES):
        try:
            client = get_groq()
            response = client.chat.completions.create(
                model=AGENT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=AGENT_MAX_TOKENS,
                timeout=30,
            )
            raw = response.choices[0].message.content.strip()
            if "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = _repair_agent_json(raw)

            # Accept either p_up (new) or score (legacy fallback)
            p_up_val = result.get("p_up", result.get("score", 0.5))
            return AgentVerdict(
                agent_name=agent_config["name"],
                agent_type=agent_config.get("type", "unknown"),
                stance=stance,
                p_up=_clamp(float(p_up_val)),
                confidence=_clamp(float(result.get("confidence", 0.5))),
                reasoning=result.get("reasoning", ""),
                key_points=result.get("key_points", []),
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and attempt < MAX_RETRIES - 1:
                wait = _backoff_wait(attempt, err_str)
                print(f"      ⏳ {agent_config['name']} rate-limited, sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            return AgentVerdict(
                agent_name=agent_config["name"],
                agent_type=agent_config.get("type", "unknown"),
                stance=stance,
                p_up=0.5,
                confidence=0.1,
                reasoning=f"Analysis error: {e}",
                key_points=[],
            )


def run_all_agents(
    signal: StockSignal,
    data: StockData,
    messages_context: str,
    chart_context: str,
) -> tuple[list[AgentVerdict], list[AgentVerdict]]:
    """
    Run all 6 agents SEQUENTIALLY with delay between calls.
    Stays under Groq's 70b 12k-TPM free-tier cap by spreading the calls.
    Total runtime ≈ 50–90 seconds per stock.
    """
    total = len(BULL_AGENTS) + len(BEAR_AGENTS)
    print(f"\n🤖 Running {total} agents sequentially for {signal.ticker} (delay {AGENT_DELAY_SEC:.0f}s)...")

    tasks = [("bull", a) for a in BULL_AGENTS] + [("bear", a) for a in BEAR_AGENTS]

    bull_verdicts = []
    bear_verdicts = []

    for i, (stance, agent) in enumerate(tasks):
        verdict = run_agent(agent, stance, signal, data, messages_context, chart_context)
        emoji = "🟢" if stance == "bull" else "🔴"
        failed = verdict.confidence <= 0.1 and verdict.reasoning.startswith("Analysis error")
        marker = "❌" if failed else "✅"
        print(f"   {emoji} {marker} {verdict.agent_name}: p_up={verdict.p_up:.2f} conf={verdict.confidence:.2f}")

        if stance == "bull":
            bull_verdicts.append(verdict)
        else:
            bear_verdicts.append(verdict)

        # Spread out calls to stay under TPM limit, but skip delay after the last one
        if i < len(tasks) - 1:
            time.sleep(AGENT_DELAY_SEC)

    return bull_verdicts, bear_verdicts

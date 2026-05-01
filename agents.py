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
# Delay between sequential agent calls — stays under Groq's 12k TPM free tier.
# Each call ≈ 1500-2000 tokens; spacing them out lets the rolling window clear.
AGENT_DELAY_SEC = float(os.environ.get("AGENT_DELAY_SEC", 7))
# Cap exponential backoff on 429s
MAX_BACKOFF_SEC = 90


@dataclass
class AgentVerdict:
    agent_name: str
    agent_type: str       # strategy category for stats grouping
    stance: Literal["bull", "bear"]
    score: float          # 0.0–1.0 (how strong the argument is)
    confidence: float     # 0.0–1.0 (how confident the agent is)
    reasoning: str
    key_points: list[str]


BULL_AGENTS = [
    {
        "name": "Momentum Analyst",
        "type": "technical_momentum",
        "prompt": (
            "You are a bullish momentum analyst. Analyze recent price direction "
            "and check for growing buying pressure. Look at daily/weekly/monthly % changes, "
            "whether the stock is consistently rising, and if there's a higher lows pattern."
        ),
    },
    {
        "name": "Volume Bull",
        "type": "technical_volume",
        "prompt": (
            "You are a bullish volume analyst. Analyze trading volume. "
            "Look for: above-average volume on up days (accumulation), "
            "today's volume vs 10-day average ratio, and signs of institutional buying."
        ),
    },
    {
        "name": "Technical Bull",
        "type": "technical_indicators",
        "prompt": (
            "You are a bullish technical analyst. Check: RSI below 70 (not overbought), "
            "stock above MA50 and MA200, distance from 52-week high, "
            "and whether the suggested entry is above strong support. "
            "Pay special attention to chart analysis data if available."
        ),
    },
    {
        "name": "Risk/Reward Analyst",
        "type": "risk_management",
        "prompt": (
            "You are a bullish R:R analyst. Calculate the risk/reward ratio. "
            "The signal is attractive if: R:R >= 2, stop loss is not too far from entry, "
            "and the target is realistic based on ATR volatility."
        ),
    },
    {
        "name": "Sentiment Bull",
        "type": "sentiment",
        "prompt": (
            "You are a bullish sentiment analyst. Analyze recent Discord messages about this stock. "
            "Look for: positive mentions, how many people are talking about it, "
            "whether there's consensus, and if people have entered successfully before."
        ),
    },
]

BEAR_AGENTS = [
    {
        "name": "Reversal Detector",
        "type": "technical_pattern",
        "prompt": (
            "You are a bearish reversal analyst. "
            "Look for: lower highs, support breakdown, divergence between price and volume, "
            "and whether the stock is near strong resistance."
        ),
    },
    {
        "name": "Overbought Scanner",
        "type": "technical_oscillator",
        "prompt": (
            "You are a bearish overbought analyst. "
            "Check: RSI above 70, distance from 52-week high, how much it has risen recently, "
            "and whether volume declined during the rise (bearish divergence)."
        ),
    },
    {
        "name": "Market Condition Bear",
        "type": "macro_trend",
        "prompt": (
            "You are a bearish macro analyst. Check: is the stock below MA200 "
            "(primary downtrend), is the sector weak, "
            "and is the stock failing to hold support levels."
        ),
    },
    {
        "name": "Stop Loss Proximity",
        "type": "risk_management",
        "prompt": (
            "You are a bearish risk analyst. Calculate: "
            "how far the SL is from entry in %, whether SL is below clear support, "
            "and whether normal ATR volatility could trigger the stop."
        ),
    },
    {
        "name": "Counter Trend Bear",
        "type": "macro_trend",
        "prompt": (
            "You are a bearish trend analyst. Check if the suggested entry is against the main trend. "
            "If the stock is in a long-term downtrend — a long entry is risky. "
            "Analyze the 20-day and 5-day trend. Check chart analysis if available."
        ),
    },
]

SYSTEM_BASE = """You are a professional stock analysis agent. Receive the stock data and signal, 
then return ONLY valid JSON in this exact format:

{
  "score": <number between 0.0 and 1.0>,
  "confidence": <number between 0.0 and 1.0>,
  "reasoning": "<brief explanation in English, 2-3 sentences>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"]
}

score = how strong your argument is (1.0 = very strong, 0.0 = very weak)
confidence = how confident you are in your analysis (based on available data)
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
{messages_context[:1500] if messages_context else 'No additional messages'}
"""


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _repair_agent_json(raw: str) -> dict:
    score = re.search(r'"score"\s*:\s*([\d.]+)', raw)
    confidence = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
    reasoning = re.search(r'"reasoning"\s*:\s*"([^"]*)', raw)
    if score:
        return {
            "score": float(score.group(1)),
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
                model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1000,
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

            return AgentVerdict(
                agent_name=agent_config["name"],
                agent_type=agent_config.get("type", "unknown"),
                stance=stance,
                score=_clamp(float(result.get("score", 0.5))),
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
                score=0.5,
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
    Run all 10 agents SEQUENTIALLY with delay between calls.
    This stays under Groq's free-tier 12k TPM cap by spreading ~17k tokens
    across 70+ seconds rather than burning them in 10 seconds.
    Total runtime ≈ 80–120 seconds per stock.
    """
    print(f"\n🤖 Running 10 agents sequentially for {signal.ticker} (delay {AGENT_DELAY_SEC:.0f}s)...")

    tasks = [("bull", a) for a in BULL_AGENTS] + [("bear", a) for a in BEAR_AGENTS]

    bull_verdicts = []
    bear_verdicts = []

    for i, (stance, agent) in enumerate(tasks):
        verdict = run_agent(agent, stance, signal, data, messages_context, chart_context)
        emoji = "🟢" if stance == "bull" else "🔴"
        failed = verdict.confidence <= 0.1 and verdict.reasoning.startswith("Analysis error")
        marker = "❌" if failed else "✅"
        print(f"   {emoji} {marker} {verdict.agent_name}: score={verdict.score:.2f}")

        if stance == "bull":
            bull_verdicts.append(verdict)
        else:
            bear_verdicts.append(verdict)

        # Spread out calls to stay under TPM limit, but skip delay after the last one
        if i < len(tasks) - 1:
            time.sleep(AGENT_DELAY_SEC)

    return bull_verdicts, bear_verdicts

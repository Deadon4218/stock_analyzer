import json
import os
from groq_client import get_groq
from discord_reader import download_image_as_base64


CHART_ANALYSIS_PROMPT = """You are an expert stock chart analyst. Analyze this TradingView chart image 
and extract all visible technical information.

Return ONLY valid JSON:
{
  "ticker": "SYMBOL or null if not visible",
  "timeframe": "1D, 1W, 1M, etc.",
  "trend": "uptrend / downtrend / sideways",
  "current_price": <number or null>,
  "support_levels": [<numbers>],
  "resistance_levels": [<numbers>],
  "fibonacci_levels": {"0%": <num>, "38.2%": <num>, "50%": <num>, "61.8%": <num>, "100%": <num>},
  "indicators": {
    "rsi": <number or null>,
    "atr": <number or null>,
    "above_ma50": <true/false/null>,
    "above_ma150": <true/false/null>,
    "above_ma200": <true/false/null>
  },
  "patterns": ["list of visible patterns: breakout, breakdown, double bottom, etc."],
  "key_observations": ["2-3 short observations about the chart in English"]
}

If something is not visible on the chart, set it to null or empty array.
"""


def analyze_chart_image(image_url: str) -> dict | None:
    """
    Download a chart image and analyze it with Llama 4 Scout vision model.
    Returns analysis dict or None on failure.
    """
    print(f"   📸 Downloading chart image...")
    b64_data = download_image_as_base64(image_url)
    if not b64_data:
        return None

    print(f"   🔍 Analyzing chart with Vision AI...")
    try:
        client = get_groq()
        response = client.chat.completions.create(
            model=os.environ.get("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CHART_ANALYSIS_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": b64_data},
                        },
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=800,
            timeout=30,
        )

        raw = response.choices[0].message.content.strip()
        if "</think>" in raw:
            raw = raw.split("</think>")[-1].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    except Exception as e:
        print(f"   ⚠️  Chart analysis failed: {e}")
        return None


def analyze_all_images(image_urls: list[str], max_images: int = 3) -> list[dict]:
    """
    Analyze multiple chart images. Limits to max_images to save API calls.
    Returns list of analysis dicts.
    """
    results = []
    for url in image_urls[:max_images]:
        analysis = analyze_chart_image(url)
        if analysis:
            results.append(analysis)
    return results


def format_chart_analysis(analyses: list[dict]) -> str:
    """
    Format chart analyses into a readable string for agent context.
    """
    if not analyses:
        return "No chart images available."

    parts = []
    for i, a in enumerate(analyses, 1):
        lines = [f"Chart {i}:"]
        if a.get("trend"):
            lines.append(f"  Trend: {a['trend']}")
        if a.get("current_price"):
            lines.append(f"  Current price: ${a['current_price']}")
        if a.get("timeframe"):
            lines.append(f"  Timeframe: {a['timeframe']}")

        supports = a.get("support_levels", [])
        if supports:
            lines.append(f"  Support levels: {', '.join(str(s) for s in supports)}")

        resistances = a.get("resistance_levels", [])
        if resistances:
            lines.append(f"  Resistance levels: {', '.join(str(r) for r in resistances)}")

        fib = a.get("fibonacci_levels", {})
        if fib:
            fib_str = ", ".join(f"{k}: {v}" for k, v in fib.items() if v)
            if fib_str:
                lines.append(f"  Fibonacci: {fib_str}")

        indicators = a.get("indicators", {})
        if indicators.get("rsi"):
            lines.append(f"  RSI: {indicators['rsi']}")
        if indicators.get("atr"):
            lines.append(f"  ATR: {indicators['atr']}")

        patterns = a.get("patterns", [])
        if patterns:
            lines.append(f"  Patterns: {', '.join(patterns)}")

        observations = a.get("key_observations", [])
        for obs in observations:
            lines.append(f"  → {obs}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)

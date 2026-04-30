"""
Compute accuracy stats from analyses.jsonl.
Used by Telegram /stats commands and the dashboard.
"""
from collections import defaultdict
from analyses_log import read_all

# Threshold above which an agent's individual score is treated as "voted yes for their stance"
HIGH_SCORE = 0.6
# Threshold above which we say the overall verdict was ENTRY
ENTRY_THRESHOLD = 0.67


def _is_resolved(record: dict) -> bool:
    """Has a definitive outcome (not still_open / no_data)."""
    return record.get("outcome") in ("tp_hit", "sl_hit", "expired")


def overall_stats() -> dict:
    """Return overall verdict accuracy."""
    records = read_all()
    resolved = [r for r in records if _is_resolved(r)]

    total = len(resolved)
    entries = [r for r in resolved if r["verdict"] == "ENTRY"]
    skips = [r for r in resolved if r["verdict"] == "SKIP"]

    entry_wins = sum(1 for r in entries if r["outcome"] == "tp_hit")
    skip_correct = sum(1 for r in skips if r["outcome"] in ("sl_hit", "expired"))

    return {
        "total_logged": len(records),
        "total_resolved": total,
        "entries": len(entries),
        "entry_wins": entry_wins,
        "entry_win_rate": round(entry_wins / len(entries), 3) if entries else None,
        "skips": len(skips),
        "skip_avoided_loss": skip_correct,
        "skip_correct_rate": round(skip_correct / len(skips), 3) if skips else None,
        "still_open": sum(1 for r in records if r.get("outcome") in (None, "still_open")),
    }


def agent_accuracy() -> list[dict]:
    """Per-agent: did they vote correctly given the outcome?"""
    records = read_all()
    resolved = [r for r in records if _is_resolved(r)]

    # agent_name -> (correct, total)
    counts = defaultdict(lambda: [0, 0])

    for r in resolved:
        outcome = r["outcome"]
        # "Bullish" outcome = TP hit. "Bearish" outcome = SL hit or expired.
        bullish_outcome = (outcome == "tp_hit")

        for a in r.get("agents", []):
            if a["score"] < HIGH_SCORE:
                continue  # only count strong opinions

            agent_correct = (
                (a["stance"] == "bull" and bullish_outcome) or
                (a["stance"] == "bear" and not bullish_outcome)
            )
            counts[a["name"]][0] += 1 if agent_correct else 0
            counts[a["name"]][1] += 1

    return [
        {
            "agent": name,
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 3) if total else None,
        }
        for name, (correct, total) in sorted(counts.items(), key=lambda kv: -(kv[1][0] / kv[1][1] if kv[1][1] else 0))
    ]


def strategy_accuracy() -> list[dict]:
    """Aggregate accuracy by agent_type (strategy category)."""
    records = read_all()
    resolved = [r for r in records if _is_resolved(r)]

    counts = defaultdict(lambda: [0, 0])

    for r in resolved:
        bullish_outcome = (r["outcome"] == "tp_hit")
        for a in r.get("agents", []):
            if a["score"] < HIGH_SCORE:
                continue
            t = a.get("type", "unknown")
            agent_correct = (
                (a["stance"] == "bull" and bullish_outcome) or
                (a["stance"] == "bear" and not bullish_outcome)
            )
            counts[t][0] += 1 if agent_correct else 0
            counts[t][1] += 1

    return [
        {
            "strategy": t,
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 3) if total else None,
        }
        for t, (correct, total) in sorted(counts.items(), key=lambda kv: -(kv[1][0] / kv[1][1] if kv[1][1] else 0))
    ]


def ticker_history(ticker: str, limit: int = 10) -> list[dict]:
    records = read_all()
    matches = [r for r in records if r["ticker"].upper() == ticker.upper()]
    return matches[-limit:]


def format_overall(stats: dict) -> str:
    if stats["total_resolved"] == 0:
        return (
            f"📊 <b>Stats</b>\n\n"
            f"Total analyses logged: {stats['total_logged']}\n"
            f"Still pending outcome: {stats['still_open']}\n\n"
            f"<i>Need at least a few resolved outcomes to compute accuracy. "
            f"Wait for the daily outcome tracker to run.</i>"
        )

    lines = ["📊 <b>Overall Stats</b>", ""]
    lines.append(f"Total analyses: {stats['total_logged']}")
    lines.append(f"Resolved: {stats['total_resolved']}")
    lines.append(f"Still open: {stats['still_open']}")
    lines.append("")

    if stats["entries"]:
        wr = stats["entry_win_rate"] or 0
        lines.append(f"<b>ENTRY signals:</b> {stats['entries']}")
        lines.append(f"  Wins (TP hit): {stats['entry_wins']}")
        lines.append(f"  Win rate: {wr:.1%}")

    if stats["skips"]:
        cr = stats["skip_correct_rate"] or 0
        lines.append(f"\n<b>SKIP signals:</b> {stats['skips']}")
        lines.append(f"  Correctly avoided loss: {stats['skip_avoided_loss']}")
        lines.append(f"  Correct rate: {cr:.1%}")

    return "\n".join(lines)


def format_agent_table(rows: list[dict]) -> str:
    if not rows:
        return "📊 <b>Agent stats</b>\n\nNo resolved outcomes yet."
    lines = ["🤖 <b>Agent Accuracy</b>", "<i>(when scoring ≥ 0.6)</i>", ""]
    for row in rows:
        if row["accuracy"] is None:
            continue
        lines.append(
            f"  {row['agent']}: {row['accuracy']:.1%} ({row['correct']}/{row['total']})"
        )
    return "\n".join(lines)


def format_strategy_table(rows: list[dict]) -> str:
    if not rows:
        return "📊 <b>Strategy stats</b>\n\nNo resolved outcomes yet."
    lines = ["🎯 <b>Strategy Accuracy</b>", "<i>(by agent type)</i>", ""]
    for row in rows:
        if row["accuracy"] is None:
            continue
        lines.append(
            f"  {row['strategy']}: {row['accuracy']:.1%} ({row['correct']}/{row['total']})"
        )
    return "\n".join(lines)


def format_ticker_history(ticker: str, records: list[dict]) -> str:
    if not records:
        return f"📜 No history for <b>{ticker}</b>"
    lines = [f"📜 <b>{ticker.upper()} history</b> (last {len(records)})", ""]
    for r in records:
        emoji = "✅" if r["verdict"] == "ENTRY" else "❌"
        outcome = r.get("outcome") or "open"
        outcome_emoji = {"tp_hit": "🟢", "sl_hit": "🔴", "expired": "⚪", "still_open": "⏳", "open": "⏳"}.get(outcome, "?")
        lines.append(
            f"{emoji} {r['ts'][:10]} | bull {r['bull_prob']:.0%} | {outcome_emoji} {outcome}"
        )
    return "\n".join(lines)

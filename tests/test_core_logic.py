import unittest
from unittest.mock import patch

from agents import AgentVerdict
from aggregator import aggregate, calculate_price_levels
from signal_parser import StockSignal
from stock_data import StockData
from stats import agent_accuracy


def verdict(name: str, stance: str, p_up: float, confidence: float = 1.0) -> AgentVerdict:
    return AgentVerdict(
        agent_name=name,
        agent_type="test",
        stance=stance,
        p_up=p_up,
        confidence=confidence,
        reasoning="test",
        key_points=[],
    )


def stock_data(price: float = 100.0, atr: float = 5.0) -> StockData:
    return StockData(
        ticker="TEST",
        current_price=price,
        price_1d_ago=price,
        price_5d_ago=price,
        price_20d_ago=price,
        volume_today=1000,
        volume_avg_10d=1000,
        high_52w=120,
        low_52w=80,
        rsi_14=50,
        atr_14=atr,
        above_200ma=True,
        above_50ma=True,
        market_cap=None,
    )


class CoreLogicTest(unittest.TestCase):
    def test_long_entry_uses_high_up_probability(self):
        result = aggregate(
            "TEST",
            [verdict("bull", "bull", 0.8)],
            [verdict("bear", "bear", 0.7)],
            direction="long",
        )
        self.assertTrue(result.should_enter)

    def test_short_entry_uses_low_up_probability(self):
        result = aggregate(
            "TEST",
            [verdict("bull", "bull", 0.25)],
            [verdict("bear", "bear", 0.35)],
            direction="short",
        )
        self.assertTrue(result.should_enter)

    def test_short_price_levels_put_sl_above_and_tp_below_entry(self):
        levels = calculate_price_levels(
            StockSignal(ticker="TEST", direction="short"),
            stock_data(price=100, atr=4),
            [],
        )
        self.assertEqual(levels.entry, 100)
        self.assertEqual(levels.stop_loss, 106)
        self.assertEqual(levels.take_profit, 88)
        self.assertEqual(levels.rr_ratio, 2.0)

    def test_directional_agent_accuracy_counts_low_p_up_as_bearish(self):
        records = [
            {
                "outcome": "sl_hit",
                "direction": "long",
                "score_contract": "directional_p_up",
                "agents": [
                    {"name": "Bearish read", "type": "test", "stance": "bear", "score": 0.2},
                    {"name": "Bullish read", "type": "test", "stance": "bull", "score": 0.8},
                ],
            }
        ]
        with patch("stats.read_all", return_value=records):
            rows = {row["agent"]: row for row in agent_accuracy()}
        self.assertEqual(rows["Bearish read"]["accuracy"], 1.0)
        self.assertEqual(rows["Bullish read"]["accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()

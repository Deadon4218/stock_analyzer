import yfinance as yf
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass
class StockData:
    ticker: str
    current_price: float
    price_1d_ago: float
    price_5d_ago: float
    price_20d_ago: float
    volume_today: int
    volume_avg_10d: float
    high_52w: float
    low_52w: float
    rsi_14: Optional[float]
    atr_14: Optional[float]
    above_200ma: bool
    above_50ma: bool
    market_cap: Optional[float]
    sector: str = ""
    error: str = ""

    def pct_from_high(self) -> float:
        if self.high_52w == 0:
            return 0
        return round((self.current_price - self.high_52w) / self.high_52w * 100, 2)

    def volume_spike(self) -> float:
        if self.volume_avg_10d == 0:
            return 0
        return round(self.volume_today / self.volume_avg_10d, 2)

    def summary(self) -> str:
        rsi_str = f"{self.rsi_14:.1f}" if self.rsi_14 else "N/A"
        return (
            f"Price: ${self.current_price:.2f} | "
            f"RSI: {rsi_str} | "
            f"Volume: {self.volume_spike():.1f}x avg | "
            f"From 52w high: {self.pct_from_high():.1f}% | "
            f"Above MA200: {'✅' if self.above_200ma else '❌'} | "
            f"Above MA50: {'✅' if self.above_50ma else '❌'}"
        )


def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else None


def _calc_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr), 4) if pd.notna(atr) else None


def fetch_stock_data(ticker: str) -> StockData:
    """Fetch market data from yfinance."""
    empty = StockData(
        ticker=ticker, current_price=0, price_1d_ago=0,
        price_5d_ago=0, price_20d_ago=0, volume_today=0,
        volume_avg_10d=0, high_52w=0, low_52w=0,
        rsi_14=None, atr_14=None, above_200ma=False,
        above_50ma=False, market_cap=None,
    )

    try:
        yf_ticker = yf.Ticker(ticker)
        hist = yf_ticker.history(period="1y")

        if hist.empty or len(hist) < 20:
            empty.error = f"No historical data for {ticker}"
            return empty

        closes = hist["Close"]
        info = yf_ticker.info or {}

        current = float(closes.iloc[-1])
        price_1d = float(closes.iloc[-2]) if len(closes) >= 2 else current
        price_5d = float(closes.iloc[-5]) if len(closes) >= 5 else current
        price_20d = float(closes.iloc[-20]) if len(closes) >= 20 else current

        ma50 = float(closes.tail(50).mean())
        ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else float(closes.mean())

        vol_today = int(hist["Volume"].iloc[-1])
        vol_avg = float(hist["Volume"].tail(10).mean())

        return StockData(
            ticker=ticker,
            current_price=current,
            price_1d_ago=price_1d,
            price_5d_ago=price_5d,
            price_20d_ago=price_20d,
            volume_today=vol_today,
            volume_avg_10d=vol_avg,
            high_52w=float(closes.max()),
            low_52w=float(closes.min()),
            rsi_14=_calc_rsi(closes),
            atr_14=_calc_atr(hist),
            above_200ma=current > ma200,
            above_50ma=current > ma50,
            market_cap=info.get("marketCap"),
            sector=info.get("sector", ""),
        )

    except Exception as e:
        empty.error = str(e)
        return empty

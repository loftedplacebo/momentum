from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import pandas as pd
import requests

from .universe import BASE_URL, UniverseSnapshot


class BinanceKlineDownloader:
    """Downloads completed Binance candles at the chosen interval."""

    INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000}

    def __init__(self, interval: str = "1h", session: requests.Session | None = None):
        if interval not in self.INTERVAL_MS:
            raise ValueError(f"Unsupported interval {interval}.")
        self.interval = interval
        self.session = session or requests.Session()

    def download(self, symbols: list[str], start: datetime, end: datetime, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        for symbol in symbols:
            candle_path = data_dir / f"{symbol}.csv"
            funding_path = data_dir / f"{symbol}.funding.csv"
            # A completed symbol is cacheable; this also lets interrupted downloads resume.
            if candle_path.exists() and funding_path.exists():
                continue
            frame = self._klines(symbol, start, end)
            frame.to_csv(candle_path, index=False)
            self._funding(symbol, start, end).to_csv(funding_path, index=False)
            time.sleep(0.05)

    def _klines(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        cursor = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        rows = []
        while cursor < end_ms:
            batch = self._request_json("/fapi/v1/klines", {
                "symbol": symbol, "interval": self.interval, "startTime": cursor, "endTime": end_ms, "limit": 1500,
            })
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + self.INTERVAL_MS[self.interval]
            time.sleep(0.05)
        columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        frame = pd.DataFrame(rows, columns=columns)
        if frame.empty:
            return frame
        frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume", "quote_volume"):
            frame[col] = pd.to_numeric(frame[col])
        return frame[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]]

    def _funding(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        rows = []
        while cursor < end_ms:
            batch = self._request_json("/fapi/v1/fundingRate", {
                "symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000,
            })
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1]["fundingTime"]) + 1
            time.sleep(0.05)
        if not rows:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])
        return pd.DataFrame({
            "timestamp": pd.to_datetime([x["fundingTime"] for x in rows], unit="ms", utc=True),
            "funding_rate": pd.to_numeric([x["fundingRate"] for x in rows]),
        })

    def _request_json(self, path: str, params: dict) -> list:
        """Retry transient Binance timeouts/rate limits without losing progress."""
        for attempt in range(4):
            try:
                response = self.session.get(BASE_URL + path, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("Unreachable retry state.")


def load_price_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    prices = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
        if frame.index.tz is None:
            frame.index = frame.index.tz_localize("UTC")
        prices[symbol] = frame
    return prices


def load_funding_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.Series]:
    """Actual Binance funding rates at their timestamp; missing data is handled as zero."""
    funding: dict[str, pd.Series] = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}.funding.csv"
        if path.exists():
            frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
                continue
            if frame.index.tz is None:
                frame.index = frame.index.tz_localize("UTC")
            funding[symbol] = frame["funding_rate"].astype(float)
    return funding


def load_open_interest_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.Series]:
    """Load optional hourly OI history from ``SYMBOL.open_interest.csv``.

    Expected columns are ``timestamp`` and ``open_interest``. Binance's public
    OI-history endpoint only retains a limited recent window, so use a vendor
    archive for full-year OI-enhanced backtests.
    """
    open_interest: dict[str, pd.Series] = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}.open_interest.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
        if frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or "open_interest" not in frame:
            continue
        if frame.index.tz is None:
            frame.index = frame.index.tz_localize("UTC")
        open_interest[symbol] = frame["open_interest"].astype(float)
    return open_interest


def combine_price_data(data_dir: Path, symbols: list[str], output_path: Path) -> None:
    """Create one long-format hourly-price CSV for analysis and reproducible research."""
    frames = []
    for symbol in symbols:
        path = data_dir / f"{symbol}.csv"
        if path.exists():
            frame = pd.read_csv(path)
            if not frame.empty:
                frame.insert(0, "symbol", symbol)
                frames.append(frame)
    if not frames:
        raise ValueError("No price files available to combine.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).sort_values(["timestamp", "symbol"]).to_csv(output_path, index=False)


def default_date_range(months: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return end - timedelta(days=round(months * 30.4375)), end

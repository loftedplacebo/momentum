from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

import pandas as pd
import requests

from .config import BacktestConfig
from .universe import UniverseSnapshot


KUCOIN_FUTURES_URL = "https://api-futures.kucoin.com/api/v1"


class KuCoinFuturesProvider:
    """Current liquid USDT-settled linear perpetuals, saved as a reproducible snapshot."""

    def __init__(self, config: BacktestConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def select(self, limit: int = 100) -> UniverseSnapshot:
        contracts = self._get("/contracts/active")
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        candidates: list[tuple[str, float]] = []
        for contract in contracts:
            listed_at = int(contract.get("firstOpenDate") or 0)
            if (contract.get("isInverse") or contract.get("settleCurrency") != "USDT"
                    or contract.get("status") != "Open"
                    or contract.get("baseCurrency") in self.config.stablecoin_bases):
                continue
            if listed_at and now_ms - listed_at < self.config.min_listing_age_days * 86_400_000:
                continue
            turnover = float(contract.get("turnoverOf24h") or 0.0)
            if turnover >= self.config.min_quote_volume:
                candidates.append((contract["symbol"], turnover))
        candidates.sort(key=lambda item: item[1], reverse=True)
        return UniverseSnapshot([symbol for symbol, _ in candidates[:limit]], datetime.now(timezone.utc).isoformat())

    def _get(self, path: str, params: dict | None = None):
        response = self.session.get(KUCOIN_FUTURES_URL + path, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "200000":
            raise RuntimeError(f"KuCoin API error: {payload}")
        return payload["data"]


class KuCoinKlineDownloader:
    """Public KuCoin Futures candles and funding history at 15-minute or hourly resolution."""

    GRANULARITY_MINUTES = {"15m": 15, "1h": 60}
    INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000}

    def __init__(self, interval: str = "1h", session: requests.Session | None = None):
        if interval not in self.GRANULARITY_MINUTES:
            raise ValueError(f"Unsupported interval {interval}.")
        self.interval = interval
        self.session = session or requests.Session()

    def download(self, symbols: list[str], start: datetime, end: datetime, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        for symbol in symbols:
            candle_path = data_dir / f"{symbol}.csv"
            funding_path = data_dir / f"{symbol}.funding.csv"
            if candle_path.exists() and funding_path.exists():
                continue
            self._klines(symbol, start, end).to_csv(candle_path, index=False)
            self._funding(symbol, start, end).to_csv(funding_path, index=False)
            time.sleep(0.08)

    def _klines(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        cursor = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        rows: list[list] = []
        while cursor < end_ms:
            batch = self._request("/kline/query", {
                "symbol": symbol, "granularity": self.GRANULARITY_MINUTES[self.interval],
                "from": cursor, "to": end_ms,
            })
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + self.INTERVAL_MS[self.interval]
            time.sleep(0.08)
        columns = ["open_time", "open", "close", "high", "low", "volume", "quote_volume"]
        frame = pd.DataFrame(rows, columns=columns)
        if frame.empty:
            return frame
        frame = frame.drop_duplicates("open_time").sort_values("open_time")
        frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        for column in ("open", "high", "low", "close", "volume", "quote_volume"):
            frame[column] = pd.to_numeric(frame[column])
        return frame[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]]

    def _funding(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        rows = self._request("/contract/funding-rates", {
            "symbol": symbol, "from": int(start.timestamp() * 1000), "to": int(end.timestamp() * 1000),
        })
        if not rows:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])
        return pd.DataFrame({
            "timestamp": pd.to_datetime([item["timepoint"] for item in rows], unit="ms", utc=True),
            "funding_rate": pd.to_numeric([item["fundingRate"] for item in rows]),
        }).drop_duplicates("timestamp").sort_values("timestamp")

    def _request(self, path: str, params: dict) -> list:
        for attempt in range(4):
            try:
                response = self.session.get(KUCOIN_FUTURES_URL + path, params=params, timeout=30)
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != "200000":
                    raise RuntimeError(f"KuCoin API error: {payload}")
                return payload["data"]
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("Unreachable retry state.")

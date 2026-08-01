from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

import requests

from .config import BacktestConfig

BASE_URL = "https://fapi.binance.com"


@dataclass(frozen=True)
class UniverseSnapshot:
    symbols: list[str]
    selected_at: str

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"symbols": self.symbols, "selected_at": self.selected_at}, indent=2))

    @classmethod
    def load(cls, path: Path) -> "UniverseSnapshot":
        payload = json.loads(path.read_text())
        return cls(symbols=payload["symbols"], selected_at=payload["selected_at"])


class BinanceUniverseProvider:
    """Current liquid contracts. Save snapshots to make each run reproducible."""

    def __init__(self, config: BacktestConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def select(self, limit: int = 100) -> UniverseSnapshot:
        exchange = self._get("/fapi/v1/exchangeInfo")
        tickers = {x["symbol"]: x for x in self._get("/fapi/v1/ticker/24hr")}
        candidates: list[tuple[str, float]] = []
        for item in exchange["symbols"]:
            symbol = item["symbol"]
            listed_at = int(item.get("onboardDate", 0))
            age_ms = datetime.now(timezone.utc).timestamp() * 1000 - listed_at
            if (item.get("contractType") != "PERPETUAL" or item.get("quoteAsset") != "USDT"
                    or item.get("status") != "TRADING" or item.get("baseAsset") in self.config.stablecoin_bases):
                continue
            if listed_at and age_ms < self.config.min_listing_age_days * 86_400_000:
                continue
            ticker = tickers.get(symbol)
            volume = float(ticker.get("quoteVolume", 0)) if ticker else 0.0
            if volume >= self.config.min_quote_volume:
                candidates.append((symbol, volume))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return UniverseSnapshot([symbol for symbol, _ in candidates[:limit]], datetime.now(timezone.utc).isoformat())

    def _get(self, path: str, params: dict | None = None):
        response = self.session.get(BASE_URL + path, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

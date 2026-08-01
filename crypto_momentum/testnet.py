"""Binance USD(S)-M Futures testnet adapter.

This module deliberately defaults to dry-run.  Testnet order submission needs an
explicit command-line flag plus separately supplied testnet credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests


TESTNET_BASE_URL = "https://testnet.binancefuture.com"


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    quantity_step: Decimal
    min_quantity: Decimal


class BinanceFuturesTestnetClient:
    """Small REST client for a safe testnet paper/execution bridge."""

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 session: requests.Session | None = None) -> None:
        self.api_key = api_key or os.getenv("BINANCE_TESTNET_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET")
        self.session = session or requests.Session()

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 signed: bool = False) -> dict | list:
        params = dict(params or {})
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET for signed testnet calls.")
            params.update({"timestamp": int(time.time() * 1000), "recvWindow": 5_000})
            payload = urlencode(params, doseq=True)
            params["signature"] = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.api_key
        response = self.session.request(method, f"{TESTNET_BASE_URL}{path}", params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("code", 0) not in (0, 200):
            raise RuntimeError(f"Binance testnet error {data['code']}: {data.get('msg', data)}")
        return data

    def server_time(self) -> int:
        return int(self._request("GET", "/fapi/v1/time")["serverTime"])

    def exchange_info(self) -> dict:
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def symbol_rules(self, symbol: str) -> SymbolRules:
        info = self.exchange_info()
        details = next((item for item in info["symbols"] if item["symbol"] == symbol), None)
        if not details:
            raise ValueError(f"{symbol} is not listed on the Futures testnet.")
        lot = next(item for item in details["filters"] if item["filterType"] in {"LOT_SIZE", "MARKET_LOT_SIZE"})
        return SymbolRules(symbol, Decimal(lot["stepSize"]), Decimal(lot["minQty"]))

    def hourly_klines(self, symbol: str, limit: int = 250) -> pd.DataFrame:
        rows = self._request("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": limit})
        frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
        frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = frame[column].astype(float)
        # The current 1h candle is incomplete and must never become a signal input.
        now = pd.Timestamp.now(tz="UTC")
        frame = frame[frame["timestamp"] + pd.Timedelta(hours=1) <= now]
        return frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]

    def hourly_price_frames(self, symbols: list[str], limit: int = 250) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """Fetch completed hourly candles, retaining an error record for unavailable testnet symbols."""
        prices: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for symbol in symbols:
            try:
                frame = self.hourly_klines(symbol, limit)
                if len(frame) >= 200:
                    prices[symbol] = frame
                else:
                    errors[symbol] = "fewer than 200 completed hourly candles"
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                errors[symbol] = str(exc)
        return prices, errors

    @staticmethod
    def quantity_for_notional(notional_usdt: float, mark_price: float, rules: SymbolRules) -> str:
        raw = Decimal(str(notional_usdt)) / Decimal(str(mark_price))
        quantity = (raw / rules.quantity_step).to_integral_value(rounding=ROUND_DOWN) * rules.quantity_step
        if quantity < rules.min_quantity:
            raise ValueError(f"{rules.symbol}: ${notional_usdt:g} is below the testnet minimum quantity.")
        return format(quantity, "f")

    def validate_market_order(self, symbol: str, side: str, quantity: str) -> dict:
        """Validate a signed order with Binance's test-order endpoint; it does not execute."""
        return self._request("POST", "/fapi/v1/order/test", {"symbol": symbol, "side": side,
                             "type": "MARKET", "quantity": quantity}, signed=True)

    def submit_market_order(self, symbol: str, side: str, quantity: str, client_order_id: str) -> dict:
        """Submit an actual order to Futures testnet only, never production."""
        return self._request("POST", "/fapi/v1/order", {"symbol": symbol, "side": side,
                             "type": "MARKET", "quantity": quantity,
                             "newClientOrderId": client_order_id}, signed=True)

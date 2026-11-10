from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from config.settings import Config


def _get(name: str, default: Any) -> Any:
    value = getattr(Config, name, default)
    if value == "":
        return default
    return value


class TradingConfig:
    BASE_DIR = str(Path(__file__).resolve().parents[3])
    MODE = str(_get("TRADING_MODE", "paper") or "paper")
    STRATEGY = str(_get("TRADING_STRATEGY", "swing") or "swing").lower()
    INITIAL_CAPITAL = float(_get("INITIAL_CAPITAL", 1000.0) or 1000.0)
    EMA_TREND_PERIOD = int(_get("EMA_TREND_PERIOD", 200) or 200)
    ADX_PERIOD = int(_get("ADX_PERIOD", 14) or 14)
    EXCHANGE_NAME = str(_get("EXCHANGE_NAME", "binance") or "binance")
    SECONDARY_EXCHANGE_NAME = str(_get("SECONDARY_EXCHANGE_NAME", "") or "")
    EXCHANGE_FALLBACKS = list(_get("EXCHANGE_FALLBACKS", []) or [])
    EXCHANGE_FORCE_SPOT_MARKETS = bool(_get("EXCHANGE_FORCE_SPOT_MARKETS", True))
    PAPER_ALLOW_YFINANCE_FALLBACK = bool(_get("PAPER_ALLOW_YFINANCE_FALLBACK", False))
    PAPER_USE_SANDBOX = bool(_get("PAPER_USE_SANDBOX", False))
    EXCHANGE_TIMEOUT_MS = int(_get("EXCHANGE_TIMEOUT_MS", 3000) or 3000)
    EXCHANGE_RATE_LIMIT_MAX_CALLS = int(_get("EXCHANGE_RATE_LIMIT_MAX_CALLS", 1100) or 1100)
    EXCHANGE_RATE_LIMIT_PERIOD_SEC = int(_get("EXCHANGE_RATE_LIMIT_PERIOD_SEC", 60) or 60)
    EXCHANGE_API_ERROR_THRESHOLD = int(_get("EXCHANGE_API_ERROR_THRESHOLD", 3) or 3)
    EXCHANGE_API_PAUSE_SEC = int(_get("EXCHANGE_API_PAUSE_SEC", 3600) or 3600)
    EXCHANGE_ORDER_FETCH_RETRIES = int(_get("EXCHANGE_ORDER_FETCH_RETRIES", 2) or 2)
    EXCHANGE_ORDER_RETRY_BASE_SEC = float(_get("EXCHANGE_ORDER_RETRY_BASE_SEC", 0.2) or 0.2)
    EXCHANGE_ORDER_RETRY_MAX_SEC = float(_get("EXCHANGE_ORDER_RETRY_MAX_SEC", 1.0) or 1.0)
    EXCHANGE_MAX_TIME_DRIFT_MS = int(_get("EXCHANGE_MAX_TIME_DRIFT_MS", 2000) or 2000)
    EXCHANGE_IP_WHITELIST_REQUIRED = bool(_get("EXCHANGE_IP_WHITELIST_REQUIRED", False))
    EXCHANGE_ALLOWED_IPS = str(_get("EXCHANGE_ALLOWED_IPS", "") or "")
    ORDER_PRICE_SIDE_ROUNDING_ENABLED = bool(_get("ORDER_PRICE_SIDE_ROUNDING_ENABLED", True))
    ORDER_USE_LIMIT_CHASE = bool(_get("ORDER_USE_LIMIT_CHASE", True))
    ORDER_CHASE_TIMEOUT_SEC = float(_get("ORDER_CHASE_TIMEOUT_SEC", 3.0) or 3.0)
    ORDER_CHASE_MAX_RETRIES = int(_get("ORDER_CHASE_MAX_RETRIES", 1) or 1)
    ORDER_CHASE_POLL_SEC = float(_get("ORDER_CHASE_POLL_SEC", 0.3) or 0.3)
    ORDER_CHASE_MARKET_FALLBACK = bool(_get("ORDER_CHASE_MARKET_FALLBACK", True))
    MARKET_ORDER_MAX_SPREAD_PCT = float(_get("MARKET_ORDER_MAX_SPREAD_PCT", _get("SPREAD_MAX_PCT", 0.5)) or 0.5)
    WS_SEQUENCE_CHECK_ENABLED = bool(_get("WS_SEQUENCE_CHECK_ENABLED", True))
    WS_SEQUENCE_MAX_JUMP = int(_get("WS_SEQUENCE_MAX_JUMP", 50) or 50)
    DELIST_STATUS_RISK_FLAGS = list(_get("DELIST_STATUS_RISK_FLAGS", ["delist", "halt", "suspend", "closed"]) or [])
    DAILY_MAX_LOSS_PCT = float(_get("DAILY_MAX_LOSS_PCT", 10.0) or 10.0)
    MAX_OPEN_POSITIONS = int(_get("MAX_OPEN_POSITIONS", 5) or 5)
    DEFAULT_RISK_PERCENTAGE = float(_get("DEFAULT_RISK_PERCENTAGE", 1.0) or 1.0)
    HIGH_CONFIDENCE_RISK_PERCENTAGE = float(
        _get("HIGH_CONFIDENCE_RISK_PERCENTAGE", DEFAULT_RISK_PERCENTAGE) or DEFAULT_RISK_PERCENTAGE
    )
    RISK_PERF_MIN_TRADES = int(_get("RISK_PERF_MIN_TRADES", 5) or 5)
    RISK_PERF_WINRATE_STRONG = float(_get("RISK_PERF_WINRATE_STRONG", 0.58) or 0.58)
    RISK_PERF_WINRATE_WEAK = float(_get("RISK_PERF_WINRATE_WEAK", 0.45) or 0.45)
    RISK_PERF_UPSIDE_SOFT = float(_get("RISK_PERF_UPSIDE_SOFT", 0.04) or 0.04)
    RISK_PERF_DRAWDOWN_SOFT = float(_get("RISK_PERF_DRAWDOWN_SOFT", -0.03) or -0.03)
    RISK_PERF_MULT_MIN = float(_get("RISK_PERF_MULT_MIN", 0.70) or 0.70)
    RISK_PERF_MULT_MAX = float(_get("RISK_PERF_MULT_MAX", 1.15) or 1.15)
    RISK_ATR_TARGET_PCT = float(_get("RISK_ATR_TARGET_PCT", 2.5) or 2.5)
    RISK_ATR_MULT_MIN = float(_get("RISK_ATR_MULT_MIN", 0.70) or 0.70)
    RISK_ATR_MULT_MAX = float(_get("RISK_ATR_MULT_MAX", 1.25) or 1.25)
    RSI_BUY_MIN = float(_get("RSI_BUY_MIN", 52.0) or 52.0)
    RSI_BUY_MAX = float(_get("RSI_BUY_MAX", 68.0) or 68.0)
    RSI_SELL_MIN = float(_get("RSI_SELL_MIN", 28.0) or 28.0)
    RSI_SELL_MAX = float(_get("RSI_SELL_MAX", 48.0) or 48.0)
    BACKTEST_SLIPPAGE_BPS = float(_get("BACKTEST_SLIPPAGE_BPS", 6.0) or 6.0)
    BACKTEST_SPREAD_BPS = float(_get("BACKTEST_SPREAD_BPS", 4.0) or 4.0)
    BACKTEST_SPREAD_MAX_PCT = float(_get("BACKTEST_SPREAD_MAX_PCT", 1.0) or 1.0)
    BACKTEST_WINRATE_MODE = bool(_get("BACKTEST_WINRATE_MODE", True))
    BACKTEST_TP_ATR_MULT = float(_get("BACKTEST_TP_ATR_MULT", 1.0) or 1.0)
    BACKTEST_SL_ATR_MULT = float(_get("BACKTEST_SL_ATR_MULT", 6.0) or 6.0)
    BACKTEST_MIN_SCORE_BUFFER = int(_get("BACKTEST_MIN_SCORE_BUFFER", 1) or 1)
    BACKTEST_TREND_ONLY = bool(_get("BACKTEST_TREND_ONLY", True))
    BACKTEST_LONG_ONLY = bool(_get("BACKTEST_LONG_ONLY", True))
    BACKTEST_USE_OHLC_PATH = bool(_get("BACKTEST_USE_OHLC_PATH", True))
    BACKTEST_TP_PRIORITY = bool(_get("BACKTEST_TP_PRIORITY", True))
    TELEGRAM_ENABLED = bool(_get("TELEGRAM_ENABLED", False))
    API_KEY = str(_get("API_KEY", "") or "")
    API_SECRET = str(_get("API_SECRET", "") or "")
    TAKER_FEE_PERCENT = _get("TAKER_FEE_PERCENT", 0.1)
    STRATEGY_CONFIGS = deepcopy(
        _get(
            "STRATEGY_CONFIGS",
            {
                "swing": {
                    "confluence_threshold": 4,
                    "params": {
                        "ema_short": 12,
                        "ema_long": 26,
                        "rsi": 14,
                        "macd_fast": 12,
                        "macd_slow": 26,
                        "macd_signal": 9,
                        "atr": 14,
                        "volume_ma": 20,
                    },
                    "risk": {"tsl_atr_multiple": 1.5, "tp_atr_multiple": 3.0},
                },
                "scalping": {
                    "confluence_threshold": 3,
                    "params": {
                        "ema_short": 9,
                        "ema_long": 21,
                        "rsi": 7,
                        "macd_fast": 8,
                        "macd_slow": 21,
                        "macd_signal": 5,
                        "atr": 14,
                        "volume_ma": 10,
                    },
                    "risk": {"tsl_atr_multiple": 1.2, "tp_atr_multiple": 2.0},
                },
            },
        )
        or {}
    )

    def __init__(self):
        for key, value in self.__class__.__dict__.items():
            if key.isupper():
                setattr(self, key, deepcopy(value))

    @classmethod
    def validate_runtime_schema(cls) -> list[str]:
        errors: list[str] = []
        try:
            spread = float(getattr(cls, "MARKET_ORDER_MAX_SPREAD_PCT", 0.0) or 0.0)
            if spread < 0:
                errors.append("MARKET_ORDER_MAX_SPREAD_PCT must be >= 0.")
        except Exception:
            errors.append("MARKET_ORDER_MAX_SPREAD_PCT must be numeric.")
        return errors

    @classmethod
    def validate_exchange_ip_whitelist(cls) -> list[str]:
        if str(getattr(cls, "MODE", "paper") or "").lower() != "live":
            return []
        if not bool(getattr(cls, "EXCHANGE_IP_WHITELIST_REQUIRED", False)):
            return []
        allowed = [part.strip() for part in str(getattr(cls, "EXCHANGE_ALLOWED_IPS", "") or "").split(",") if part.strip()]
        if allowed:
            return []
        return ["EXCHANGE_ALLOWED_IPS required when EXCHANGE_IP_WHITELIST_REQUIRED=1 in live mode."]


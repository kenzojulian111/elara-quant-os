import json
import os
import sys
from typing import Dict, List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config.settings import Config
from skills.CryptoScreenerSkill import CryptoScreenerSkill
from skills.StockScreenerSkill import StockScreenerSkill


def _bbca_verdict(stock_skill: StockScreenerSkill) -> Dict:
    symbol = "BBCA.JK"
    df = stock_skill._fetch_ohlcv(symbol)
    if df is None or df.empty:
        return {"symbol": symbol, "status": "no_data", "verdict": "N/A"}

    bench = stock_skill._fetch_ohlcv(stock_skill.benchmark_symbol)
    bench_ret = None
    if bench is not None and len(bench) > Config.STOCK_RS_DAYS:
        bench_ret = ((bench["close"].iloc[-1] / bench["close"].iloc[-Config.STOCK_RS_DAYS]) - 1) * 100

    fundamentals = stock_skill._load_fundamentals()
    candidate = stock_skill._analyze_symbol(symbol, fundamentals, bench_ret, df)
    if candidate:
        return {
            "symbol": symbol,
            "status": "qualified",
            "verdict": "LAYAK PANTAU BUY (sesuai filter screener)",
            "data": candidate,
        }

    latest = df.iloc[-1]
    ma20 = float(df["close"].rolling(20).mean().iloc[-1])
    ma50 = float(df["close"].rolling(50).mean().iloc[-1])
    ma200 = float(df["close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    atr = stock_skill._compute_atr(df).iloc[-1]
    atr_pct = float((atr / latest["close"]) * 100) if latest["close"] else 0.0
    avg_vol20 = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio = float(latest["volume"] / avg_vol20) if avg_vol20 else 0.0
    trend_ok = bool(float(latest["close"]) > ma20 > ma50 > (ma200 or ma50))
    reasons: List[str] = []
    if not trend_ok:
        reasons.append("trend belum stack bullish (close/MA20/MA50/MA200)")
    if vol_ratio < 1.0:
        reasons.append(f"volume lemah ({vol_ratio:.2f}x dari avg20)")
    return {
        "symbol": symbol,
        "status": "filtered",
        "verdict": "BELUM LAYAK BUY (menurut filter screener saat ini)",
        "reasons": reasons,
        "metrics": {
            "close": float(latest["close"]),
            "ma20": ma20,
            "ma50": ma50,
            "ma200": ma200,
            "atr_pct": atr_pct,
            "vol_ratio": vol_ratio,
        },
    }


def _crypto_verdict(crypto_skill: CryptoScreenerSkill) -> Dict:
    snap = crypto_skill.get_screen_snapshot(force_refresh=True, wait_timeout_sec=40)
    results = snap.get("results") or []
    verdict = []
    passed_symbols = {str(r.get("symbol") or "").upper(): r for r in results}

    for sym in crypto_skill._load_universe():
        if sym == crypto_skill.benchmark_symbol:
            continue
        upper_sym = sym.upper()
        if upper_sym in passed_symbols:
            row = passed_symbols[upper_sym]
            verdict.append(
                {
                    "symbol": upper_sym,
                    "status": "qualified",
                    "verdict": "LAYAK PANTAU BUY (sesuai filter screener)",
                    "score": float(row.get("score") or 0.0),
                    "setup": row.get("setup"),
                    "entry": float(row.get("entry") or 0.0),
                    "stop": float(row.get("stop") or 0.0),
                    "tp1": float(row.get("tp1") or 0.0),
                    "rr": float(row.get("rr") or 0.0),
                }
            )
            continue

        frame = crypto_skill._fetch_ohlcv_yfinance(sym)
        has_data = frame is not None and not frame.empty
        verdict.append(
            {
                "symbol": upper_sym,
                "status": "filtered" if has_data else "no_data",
                "verdict": "BELUM LAYAK BUY (tidak lolos filter saat ini)" if has_data else "N/A",
            }
        )
    return {
        "error": snap.get("error") or "",
        "count": len(results),
        "items": verdict,
    }


def main():
    stock_skill = StockScreenerSkill()
    crypto_skill = CryptoScreenerSkill()

    result = {
        "bbca": _bbca_verdict(stock_skill),
        "crypto": _crypto_verdict(crypto_skill),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


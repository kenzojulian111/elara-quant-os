# Strategy Threshold Assumptions

Dokumen ini menjelaskan asumsi threshold indikator yang dipakai strategi trading.

## Tujuan
- Menjaga konsistensi tuning antar environment.
- Menghindari magic number tersebar di kode.
- Memudahkan audit perubahan parameter.

## Swing Strategy
- `confluence_threshold`: batas skor minimum untuk entry.
- `RSI_BUY_MIN` / `RSI_BUY_MAX`: zona momentum beli sehat.
- `RSI_SELL_MAX`: area pelemahan untuk sinyal jual.
- `ADX_TREND_THRESHOLD` / `ADX_FLAT_THRESHOLD`: klasifikasi trend vs sideways.
- `ATR_VOL_PCT`: guard volatilitas tinggi.
- `SWING_*`: bobot/threshold tambahan momentum, VWAP distance, volatility mix.

## Scalping Strategy
- `confluence_threshold`: batas skor sinyal cepat.
- `SCALP_*`: threshold micro-momentum, VWAP lead/weak, volume expansion.
- `RSI_*`: tetap dipakai, tapi dengan buffer scalping (`SCALP_RSI_BUY_BUFFER`).

## Risk Context
- `MIN_RISK_MULTIPLIER` / `MAX_RISK_MULTIPLIER`: clamp risk adaptif.
- `DAILY_MAX_LOSS_PCT` / `MAX_DRAWDOWN_PCT`: risk guard global.
- `SPREAD_MAX_PCT` / `ORDER_MAX_SLIPPAGE_PCT`: execution quality gate.

## Praktik Tuning
1. Ubah threshold lewat env/runtime settings, bukan hardcode.
2. Validasi dengan backtest + walk-forward sebelum live.
3. Simpan perubahan besar beserta alasan pasar (regime) pada commit message.


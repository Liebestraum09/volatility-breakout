# volatility-breakout (KRW Market)
A virtual trading record project utilizing a volatility breakout strategy.

## What this is
Larry Williams' volatility breakout strategy applied to Upbit KRW-market coins.
Daily OHLCV data, variable-K range multiplier, Williams %R filter, trailing stop simulation.

---

## Iteration history

| Version | Key changes |
|---------|-------------|
| V3.3 | Base logic: variable K, MA, ATR, Williams %R, trailing stop |
| V4.0 | Fixed 4 lookahead bugs (volume, gap-up, TS/SL order, TS price) |
| V5.0 | Added market regime filters (BTC MA200, ATR panic guard, MA50, prev candle) |
| V6.0 | Split BTC / ALT strategies; ALT uses momentum + relative strength |
| V7.0 | Paginated 4-year data fetch; white card dashboard; grid search on ALT params |

---

## What worked
- BTC strategy showed marginal positive yield on certain parameter combos (V_0.9x, P21/P28)
- ALT momentum strategy identified a subset of coins with genuine edge: BCH, XRP, NEAR, ADA, AVAX, LINK, STX
- Realistic cost model (fee × 2 + slippage) and conservative backtest assumptions throughout

## Where it hit the wall

**The core problem is structural, not parametric.**

Daily OHLCV data cannot faithfully simulate an intraday breakout strategy:
- Entry price is unverifiable (breakout time within the day is unknown)
- Trailing stop execution price is an approximation at best
- The TS/SL same-day sequence is fundamentally ambiguous

Every fix added another patch on top of this foundation.
More filters improved noise rejection but couldn't resolve the underlying data mismatch.

A fundamental redesign is needed.
---

## Next project
**Daily-bar swing strategy on equities (KRW or USD market)**
- Multi-day holding makes daily OHLC a natural fit — no intraday ambiguity
- Entry at next-day open is realistic and reproducible
- Data via `pykrx` (KR) or `yfinance` (US)

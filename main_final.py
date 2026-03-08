import pyupbit
import pandas as pd
import numpy as np
import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# =============================================================================
# SECTION 1: CONFIGURATION
# =============================================================================

BTC_TICKER = "KRW-BTC"
ALT_TICKERS = [
    "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-DOT", "KRW-TRX", "KRW-LINK",
    "KRW-MATIC", "KRW-BCH", "KRW-SHIB", "KRW-LTC", "KRW-NEAR",
    "KRW-ATOM", "KRW-APT", "KRW-STX", "KRW-ETC", "KRW-SEI"
]

# --- Shared cost model ---
FEE_RATE     = 0.0005
SLIPPAGE     = 0.001
COST_PENALTY = (1 - FEE_RATE) ** 2 * (1 - SLIPPAGE)   # ≈ 0.9980

# ── BTC strategy params ──────────────────────────────────────────────────────
BTC_PERIODS        = [7, 14, 21, 28]
BTC_VOL_THRESHOLDS = {"V_0.8x": 0.8, "V_0.9x": 0.9, "V_1.0x": 1.0}
BTC_PROFIT_TRIGGER = 1.02
BTC_TRAILING_CB    = 0.01
BTC_MAX_LOSS_PCT   = 0.03
BTC_TREND_WINDOW   = 200
BTC_ATR_PANIC_MULT = 2.0
BTC_ATR_PANIC_WIN  = 30

# ── ALT strategy params ───────────────────────────────────────────────────────
ALT_PROFIT_TARGETS = [0.03, 0.05, 0.07]
ALT_TIMECUTS       = [1, 3, 5]
ALT_MOM_WINDOW     = 20
ALT_VOL_SURGE_MULT = 2.0
ALT_MAX_LOSS_PCT   = 0.03

# ── Backtest window ───────────────────────────────────────────────────────────
BACKTEST_DAYS = 1460    # 4 years — covers full BTC cycle including 2022 bear

# =============================================================================
# SECTION 2: DATA FETCHING — PAGINATED (4-year support)
#
# pyupbit get_ohlcv max count = 200 per call.
# To fetch N days we page backwards in time, concatenate, then deduplicate.
# Each page uses 'to' param (exclusive upper bound) set to earliest date seen.
# Rate limit: 0.1s sleep between calls to avoid 429 errors.
# =============================================================================

def fetch_and_cache_data(ticker, days=BACKTEST_DAYS):
    cache_file = f"{ticker}_{days}d_v70.pkl"
    if os.path.exists(cache_file):
        return pd.read_pickle(cache_file)

    PAGE_SIZE  = 200
    all_frames = []
    to_date    = None    # None = fetch most recent page first

    remaining = days
    while remaining > 0:
        fetch_count = min(PAGE_SIZE, remaining)

        if to_date is None:
            df_page = pyupbit.get_ohlcv(ticker, interval="day", count=fetch_count)
        else:
            df_page = pyupbit.get_ohlcv(ticker, interval="day",
                                         count=fetch_count, to=to_date)

        if df_page is None or df_page.empty:
            break

        all_frames.append(df_page)

        # Move to_date one day before the earliest row in this page
        earliest = df_page.index.min()
        to_date  = earliest.strftime("%Y-%m-%d")

        remaining -= len(df_page)
        time.sleep(0.1)

    if not all_frames:
        return None

    df = pd.concat(all_frames)
    df = df[~df.index.duplicated(keep='last')]   # remove any overlap
    df = df.sort_index()
    df.to_pickle(cache_file)
    return df

# =============================================================================
# SECTION 3: BTC STRATEGY — INDICATORS
# All values .shift(1): no lookahead bias.
# =============================================================================

def btc_calculate_indicators(df, period):
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k']     = df['noise'].rolling(period).mean().shift(1).clip(0.4, 0.6)
    df['ma']    = df['close'].rolling(period).mean().shift(1)

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr']          = tr.rolling(period).mean().shift(1)
    df['atr_baseline'] = df['atr'].rolling(BTC_ATR_PANIC_WIN).mean().shift(1)
    df['ma50']         = df['close'].rolling(50).mean().shift(1)

    hh = df['high'].rolling(14).max()
    ll = df['low'].rolling(14).min()
    df['w_r']    = ((hh - df['close']) / (hh - ll) * -100).shift(1)
    df['target'] = df['open'] + (df['high'].shift(1) - df['low'].shift(1)) * df['k']
    df['vol_ma'] = df['volume'].rolling(period).mean().shift(1)
    return df

# =============================================================================
# SECTION 4: BTC STRATEGY — ENTRY SIGNAL
# Layer A: regime (MA200) + panic guard (ATR spike)
# Layer B: long trend (MA50) + prev bullish candle
# Core: short MA trend, W%R, breakout, volume, no gap-up
# =============================================================================

def btc_entry_signal(df, vol_mult):
    btc_ma200    = df['close'].rolling(BTC_TREND_WINDOW).mean().shift(1)
    regime_ok    = df['close'].shift(1) > btc_ma200
    not_panic    = df['atr'] < df['atr_baseline'] * BTC_ATR_PANIC_MULT
    long_trend   = df['close'].shift(1) > df['ma50']
    prev_bullish = df['close'].shift(1) > df['open'].shift(1)
    short_trend    = df['open'] > df['ma']
    not_overbought = df['w_r'] < -20
    breakout       = df['high'] > df['target']
    vol_confirm    = df['volume'].shift(1) > df['vol_ma'] * vol_mult
    no_gap_up      = df['open'] <= df['target']

    return (regime_ok & not_panic & long_trend & prev_bullish &
            short_trend & not_overbought & breakout & vol_confirm & no_gap_up)

# =============================================================================
# SECTION 5: BTC STRATEGY — TS EXIT ESTIMATION (3-band)
# =============================================================================

def btc_estimate_ts_exit(entry_price, row):
    optimistic   = row['high'] * (1 - BTC_TRAILING_CB)
    pessimistic  = entry_price * BTC_PROFIT_TRIGGER
    day_range    = row['high'] - row['low']
    open_to_high = row['high'] - row['open']
    early_ratio  = 1 - (open_to_high / day_range) if day_range > 0 else 0.5
    blended = optimistic * (1 - early_ratio) + pessimistic * early_ratio
    blended = max(blended, entry_price * (BTC_PROFIT_TRIGGER - 0.005))
    return optimistic, pessimistic, blended

# =============================================================================
# SECTION 6: BTC STRATEGY — BACKTEST ENGINE
# =============================================================================

def btc_run_backtest(df, vol_mult):
    signal    = btc_entry_signal(df, vol_mult)
    trades_df = df[signal].copy()
    res_opt = []; res_pess = []; res_blend = []

    for date, row in trades_df.iterrows():
        entry   = row['target']
        sl      = max(entry - row['atr'], entry * (1 - BTC_MAX_LOSS_PCT))
        tp_trig = entry * BTC_PROFIT_TRIGGER
        ts_hit  = row['high'] >= tp_trig
        sl_hit  = row['low']  <= sl

        if ts_hit and sl_hit:
            o = p = b = sl
        elif ts_hit:
            o, p, b = btc_estimate_ts_exit(entry, row)
        elif sl_hit:
            o = p = b = sl
        else:
            idx = df.index.get_loc(date)
            if idx + 1 < len(df):
                o = p = b = df.iloc[idx + 1]['open']
            else:
                continue

        calc = lambda x: ((x / entry) * COST_PENALTY - 1) * 100
        res_opt.append(calc(o)); res_pess.append(calc(p)); res_blend.append(calc(b))

    def summ(r):
        if not r: return {"trades": 0, "win_rate": 0.0, "yield": 0.0}
        return {"trades": len(r),
                "win_rate": round(sum(1 for x in r if x > 0) / len(r) * 100, 2),
                "yield": round(sum(r), 2)}

    return {"opt": summ(res_opt), "pess": summ(res_pess), "blend": summ(res_blend)}

# =============================================================================
# SECTION 7: ALT STRATEGY — INDICATORS
# =============================================================================

def alt_calculate_indicators(df, btc_df):
    df['mom_20']  = df['close'] / df['close'].shift(ALT_MOM_WINDOW) - 1
    btc_ret       = (btc_df['close'] / btc_df['close'].shift(7) - 1).rename('btc_7d_ret')
    df            = df.join(btc_ret, how='left')
    df['alt_7d_ret'] = df['close'] / df['close'].shift(7) - 1
    df['vol_ma_20']  = df['volume'].rolling(ALT_MOM_WINDOW).mean().shift(1)

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean().shift(1)
    return df

# =============================================================================
# SECTION 8: ALT STRATEGY — ENTRY SIGNAL
# Gate 1: Alt outperforms BTC 7d return (alt season proxy)
# Gate 2: Positive 20d momentum
# Gate 3: Volume surge vs 20d avg
# Gate 4: BTC MA200 macro safety net
# =============================================================================

def alt_entry_signal(df, btc_df):
    alt_season   = df['alt_7d_ret'].shift(1) > df['btc_7d_ret'].shift(1)
    positive_mom = df['mom_20'].shift(1) > 0
    vol_surge    = df['volume'].shift(1) > df['vol_ma_20'] * ALT_VOL_SURGE_MULT
    btc_ma200    = btc_df['close'].rolling(BTC_TREND_WINDOW).mean().shift(1)
    btc_bull     = (btc_df['close'].shift(1) > btc_ma200).rename('btc_bull')
    df           = df.join(btc_bull, how='left')
    df['btc_bull'] = df['btc_bull'].fillna(False)
    return alt_season & positive_mom & vol_surge & df['btc_bull'], df

# =============================================================================
# SECTION 9: ALT STRATEGY — BACKTEST ENGINE (multi-day, grid search)
# Entry: next day open after signal
# Exit priority: TP hit → SL hit → timecut at open
# Same-day TP+SL: conservative → SL wins
# =============================================================================

def alt_run_backtest(df, signal, profit_target, timecut):
    trades_df = df[signal].copy()
    results   = []

    for date, row in trades_df.iterrows():
        entry_idx = df.index.get_loc(date)
        if entry_idx + 1 >= len(df):
            continue
        entry_price = df.iloc[entry_idx + 1]['open']
        sl_price    = entry_price * (1 - ALT_MAX_LOSS_PCT)
        tp_price    = entry_price * (1 + profit_target)
        exit_price  = None

        for hold_day in range(1, timecut + 1):
            fwd_idx = entry_idx + 1 + hold_day
            if fwd_idx >= len(df):
                break
            fwd    = df.iloc[fwd_idx]
            sl_hit = fwd['low']  <= sl_price
            tp_hit = fwd['high'] >= tp_price

            if sl_hit and tp_hit:
                exit_price = sl_price; break
            elif tp_hit:
                exit_price = tp_price; break
            elif sl_hit:
                exit_price = sl_price; break

        if exit_price is None:
            tc_idx = entry_idx + 1 + timecut
            if tc_idx < len(df):
                exit_price = df.iloc[tc_idx]['open']
            else:
                continue

        results.append(((exit_price / entry_price) * COST_PENALTY - 1) * 100)

    if not results:
        return {"trades": 0, "win_rate": 0.0, "yield": 0.0}
    return {"trades": len(results),
            "win_rate": round(sum(1 for x in results if x > 0) / len(results) * 100, 2),
            "yield": round(sum(results), 2)}

# =============================================================================
# SECTION 10: FLASK DASHBOARD — WHITE CARD THEME
# =============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Quant V7.0</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:       #f0f2f5;
            --surface:  #ffffff;
            --border:   #e4e8ef;
            --text:     #111827;
            --muted:    #6b7280;
            --faint:    #9ca3af;
            --green:    #16a34a;
            --green-bg: #f0fdf4;
            --red:      #dc2626;
            --red-bg:   #fef2f2;
            --blue:     #2563eb;
            --blue-bg:  #eff6ff;
            --purple:   #7c3aed;
            --purple-bg:#f5f3ff;
            --amber:    #d97706;
            --amber-bg: #fffbeb;
            --teal:     #0d9488;
            --teal-bg:  #f0fdfa;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 32px;
            font-size: 13px;
            min-height: 100vh;
        }

        /* ── Header ── */
        .header {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 28px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }
        .header-left h1 {
            font-size: 18px; font-weight: 700; color: var(--text);
            letter-spacing: -0.02em;
        }
        .header-left p { color: var(--muted); margin-top: 4px; font-size: 12px; }
        .header-badge {
            background: var(--blue-bg); color: var(--blue);
            font-size: 11px; font-weight: 600; padding: 4px 12px;
            border-radius: 20px; border: 1px solid #bfdbfe;
            font-family: 'JetBrains Mono', monospace;
        }

        /* ── Tabs ── */
        .tab-bar {
            display: flex; gap: 4px; margin-bottom: 20px;
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 10px; padding: 4px; width: fit-content;
        }
        .tab-btn {
            padding: 7px 20px; border-radius: 7px; border: none;
            cursor: pointer; font-family: 'Inter', sans-serif;
            font-size: 13px; font-weight: 500;
            background: transparent; color: var(--muted);
            transition: all 0.15s;
        }
        .tab-btn.active {
            background: var(--blue); color: #fff;
            box-shadow: 0 1px 4px rgba(37,99,235,0.3);
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* ── Legend strip ── */
        .legend {
            display: flex; flex-wrap: wrap; gap: 8px;
            margin-bottom: 20px;
        }
        .legend-chip {
            display: flex; align-items: center; gap: 7px;
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 8px; padding: 7px 12px;
        }
        .chip-badge {
            font-size: 10px; font-weight: 700; padding: 2px 7px;
            border-radius: 4px; font-family: 'JetBrains Mono', monospace;
            white-space: nowrap;
        }
        .chip-a  { background: var(--green-bg);  color: var(--green);  }
        .chip-b  { background: var(--blue-bg);   color: var(--blue);   }
        .chip-g  { background: var(--purple-bg); color: var(--purple); }
        .chip-sl { background: var(--amber-bg);  color: var(--amber);  }
        .chip-label { color: var(--muted); font-size: 11px; font-weight: 500; }

        /* ── Card grid ── */
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
            gap: 16px;
        }
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .card-header {
            padding: 14px 18px 12px;
            border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between;
        }
        .card-title {
            font-size: 14px; font-weight: 700;
            color: var(--text); letter-spacing: -0.01em;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-body { padding: 0; }

        /* ── Sub-section inside card ── */
        .sub-section { border-bottom: 1px solid var(--border); }
        .sub-section:last-child { border-bottom: none; }
        .sub-label {
            padding: 8px 18px 6px;
            font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
            color: var(--faint); background: #fafbfc;
            text-transform: uppercase;
        }

        /* ── Table ── */
        table { width: 100%; border-collapse: collapse; }
        th {
            padding: 8px 14px; text-align: center;
            font-size: 11px; font-weight: 600; color: var(--muted);
            background: #fafbfc;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }
        th.left { text-align: left; }
        td {
            padding: 9px 14px; text-align: center;
            font-size: 12px; border-bottom: 1px solid #f3f4f6;
            font-family: 'JetBrains Mono', monospace;
        }
        td.label-col { text-align: left; font-weight: 500; color: var(--muted); font-family: 'Inter', sans-serif; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #fafbfc; }

        /* ── Value states ── */
        .pos {
            color: var(--green); font-weight: 600;
            background: var(--green-bg);
            padding: 2px 8px; border-radius: 4px;
            display: inline-block;
        }
        .neg {
            color: var(--red); font-weight: 600;
            background: var(--red-bg);
            padding: 2px 8px; border-radius: 4px;
            display: inline-block;
        }
        .no-sig {
            color: var(--faint); font-style: italic;
            font-size: 11px; font-family: 'Inter', sans-serif;
        }

        /* ── TS columns ── */
        .col-opt   { color: var(--amber); }
        .col-blend { color: var(--text);  }
        .col-pess  { color: var(--faint); }
        .subhead   { font-size: 9px; color: var(--faint); display: block; margin-top: 1px; font-family: 'Inter', sans-serif; }

        /* ── ALT best row ── */
        .best-row td { background: var(--teal-bg) !important; }
        .best-row td.label-col { color: var(--teal); font-weight: 600; }
        .best-mark {
            font-size: 10px; background: var(--teal-bg); color: var(--teal);
            border: 1px solid #99f6e4; padding: 1px 7px; border-radius: 4px;
            font-weight: 600;
        }

        /* ── Win rate pill ── */
        .wr-high { color: var(--green); font-weight: 600; }
        .wr-mid  { color: var(--amber); font-weight: 500; }
        .wr-low  { color: var(--red);   font-weight: 500; }
    </style>
</head>
<body>

<!-- Header -->
<div class="header">
    <div class="header-left">
        <h1>Quant V7.0 — Dual Strategy Backtest</h1>
        <p>BTC: Volatility Breakout + Regime Filters &nbsp;·&nbsp; ALT: Momentum + Relative Strength &nbsp;·&nbsp; Cost: 0.05% × 2 + 0.1% slippage</p>
    </div>
    <span class="header-badge">{{ backtest_years }}yr backtest · {{ backtest_days }}d</span>
</div>

<!-- Tabs -->
<div class="tab-bar">
    <button class="tab-btn active" onclick="switchTab('btc', this)">📈 BTC Strategy</button>
    <button class="tab-btn"        onclick="switchTab('alt', this)">🚀 ALT Strategy</button>
</div>

<!-- ═══════════════════════ BTC TAB ═══════════════════════ -->
<div id="tab-btc" class="tab-content active">
    <div class="legend">
        <div class="legend-chip"><span class="chip-badge chip-a">A1</span><span class="chip-label">BTC MA200 Regime</span></div>
        <div class="legend-chip"><span class="chip-badge chip-a">A2</span><span class="chip-label">ATR Panic Guard ×2</span></div>
        <div class="legend-chip"><span class="chip-badge chip-b">B1</span><span class="chip-label">MA50 Long Trend</span></div>
        <div class="legend-chip"><span class="chip-badge chip-b">B2</span><span class="chip-label">Prev Candle Bullish</span></div>
        <div class="legend-chip"><span class="chip-badge chip-sl">SL</span><span class="chip-label">3% Hard Cap</span></div>
        <div class="legend-chip"><span class="chip-badge chip-sl">TS</span><span class="chip-label">Opt / Blend / Pess band</span></div>
    </div>

    <div class="grid">
    {% for thresh_name, period_data in btc_data.items() %}
    <div class="card">
        <div class="card-header">
            <span class="card-title">KRW-BTC</span>
            <span style="font-size:12px; color:var(--muted); font-weight:600;">{{ thresh_name }}</span>
        </div>
        <div class="card-body">
            <table>
                <thead><tr>
                    <th class="left">Period</th>
                    <th>Trades</th>
                    <th>Win%</th>
                    <th class="col-opt">Opt<span class="subhead">upper</span></th>
                    <th class="col-blend">Blend<span class="subhead">est.</span></th>
                    <th class="col-pess">Pess<span class="subhead">lower</span></th>
                </tr></thead>
                <tbody>
                {% for p_name, r in period_data.items() %}
                <tr>
                    <td class="label-col">{{ p_name }}</td>
                    {% if r.blend.trades == 0 %}
                    <td colspan="5" class="no-sig">— no signals after filters —</td>
                    {% else %}
                    <td>{{ r.blend.trades }}</td>
                    <td class="{% if r.blend.win_rate >= 55 %}wr-high{% elif r.blend.win_rate >= 45 %}wr-mid{% else %}wr-low{% endif %}">
                        {{ r.blend.win_rate }}%
                    </td>
                    <td class="col-opt"><span class="{% if r.opt.yield > 0 %}pos{% else %}neg{% endif %}">{{ r.opt.yield }}%</span></td>
                    <td class="col-blend"><span class="{% if r.blend.yield > 0 %}pos{% else %}neg{% endif %}">{{ r.blend.yield }}%</span></td>
                    <td class="col-pess"><span class="{% if r.pess.yield > 0 %}pos{% else %}neg{% endif %}">{{ r.pess.yield }}%</span></td>
                    {% endif %}
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endfor %}
    </div>
</div>

<!-- ═══════════════════════ ALT TAB ═══════════════════════ -->
<div id="tab-alt" class="tab-content">
    <div class="legend">
        <div class="legend-chip"><span class="chip-badge chip-g">G1</span><span class="chip-label">Alt Season (7d vs BTC)</span></div>
        <div class="legend-chip"><span class="chip-badge chip-g">G2</span><span class="chip-label">Positive 20d Momentum</span></div>
        <div class="legend-chip"><span class="chip-badge chip-g">G3</span><span class="chip-label">Volume Surge ×2</span></div>
        <div class="legend-chip"><span class="chip-badge chip-a">G4</span><span class="chip-label">BTC MA200 Safety</span></div>
        <div class="legend-chip"><span class="chip-badge chip-sl">SL</span><span class="chip-label">3% Hard Stop (multi-day)</span></div>
        <div class="legend-chip"><span class="chip-badge chip-b">GRID</span><span class="chip-label">TP: 3/5/7% · Cut: 1/3/5d</span></div>
    </div>

    <div class="grid">
    {% for ticker, grid_results in alt_data.items() %}
    <div class="card">
        <div class="card-header">
            <span class="card-title">{{ ticker }}</span>
            <span style="font-size:11px; color:var(--faint);">best combo highlighted</span>
        </div>
        <div class="card-body">
            <table>
                <thead><tr>
                    <th class="left">Take Profit</th>
                    <th>Timecut</th>
                    <th>Trades</th>
                    <th>Win%</th>
                    <th>Total Yield</th>
                </tr></thead>
                <tbody>
                {% for row in grid_results %}
                <tr class="{% if row.best %}best-row{% endif %}">
                    <td class="label-col">
                        {{ row.tp_label }}
                        {% if row.best %}<span class="best-mark">BEST</span>{% endif %}
                    </td>
                    <td>{{ row.tc_label }}</td>
                    {% if row.trades == 0 %}
                    <td colspan="3" class="no-sig">— no signals —</td>
                    {% else %}
                    <td>{{ row.trades }}</td>
                    <td class="{% if row.win_rate >= 55 %}wr-high{% elif row.win_rate >= 45 %}wr-mid{% else %}wr-low{% endif %}">
                        {{ row.win_rate }}%
                    </td>
                    <td><span class="{% if row.yield > 0 %}pos{% else %}neg{% endif %}">{{ row.yield }}%</span></td>
                    {% endif %}
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endfor %}
    </div>
</div>

<script>
function switchTab(name, btn) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    btn.classList.add('active');
}
</script>
</body>
</html>
"""

# =============================================================================
# SECTION 11: APP ENTRY POINT
# =============================================================================

@app.route("/")
def dashboard():
    btc_raw = fetch_and_cache_data(BTC_TICKER)

    # ── BTC strategy ─────────────────────────────────────────────────────────
    btc_data = {}
    for thresh_name, vol_mult in BTC_VOL_THRESHOLDS.items():
        period_results = {}
        for p in BTC_PERIODS:
            df = btc_calculate_indicators(btc_raw.copy(), p).dropna()
            period_results[f"P{p}"] = btc_run_backtest(df, vol_mult)
        btc_data[thresh_name] = period_results

    # ── ALT strategy (grid search) ───────────────────────────────────────────
    alt_data = {}
    for ticker in ALT_TICKERS:
        df_raw = fetch_and_cache_data(ticker)
        if df_raw is None:
            continue

        df             = alt_calculate_indicators(df_raw.copy(), btc_raw.copy()).dropna()
        signal, df     = alt_entry_signal(df, btc_raw.copy())
        grid_rows      = []

        for tp in ALT_PROFIT_TARGETS:
            for tc in ALT_TIMECUTS:
                r = alt_run_backtest(df, signal, tp, tc)
                grid_rows.append({
                    "tp_label": f"TP {int(tp*100)}%",
                    "tc_label": f"{tc}d",
                    "trades":   r["trades"],
                    "win_rate": r["win_rate"],
                    "yield":    r["yield"],
                    "best":     False,
                })

        valid = [r for r in grid_rows if r["trades"] > 0]
        if valid:
            max(valid, key=lambda x: x["yield"])["best"] = True

        alt_data[ticker] = grid_rows

    actual_days  = len(btc_raw) if btc_raw is not None else BACKTEST_DAYS
    actual_years = round(actual_days / 365, 1)

    return render_template_string(
        HTML_TEMPLATE,
        btc_data=btc_data,
        alt_data=alt_data,
        backtest_days=actual_days,
        backtest_years=actual_years,
    )


if __name__ == "__main__":
    app.run(debug=False, port=5000)
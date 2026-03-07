import pyupbit
import pandas as pd
import numpy as np
import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# --- [Configuration] ---
TICKERS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-DOT", "KRW-TRX", "KRW-LINK",
    "KRW-MATIC", "KRW-BCH", "KRW-SHIB", "KRW-LTC", "KRW-NEAR",
    "KRW-ATOM", "KRW-APT", "KRW-STX", "KRW-ETC", "KRW-SEI"
]
PERIODS = [7, 14, 21, 28]
VOL_THRESHOLDS = {"V_0.8x": 0.8, "V_0.9x": 0.9, "V_1.0x": 1.0}

PROFIT_TRIGGER = 1.02      # 2% for Trailing Stop activation
TRAILING_CALLBACK = 0.01   # 1% drop from peak
COST_PENALTY = 0.998       # -0.2% Slippage and Fee penalty

def fetch_and_cache_data(ticker, days=730):
    cache_file = f"{ticker}_2yr_v33.pkl"
    if os.path.exists(cache_file): return pd.read_pickle(cache_file)
    df = pyupbit.get_ohlcv(ticker, interval="day", count=days)
    if df is not None:
        df.to_pickle(cache_file)
        time.sleep(0.1)
    return df

def calculate_indicators(df, period):
    """V3.3 Refined Logic with Fixed 14d Williams %R"""
    # 1. Variable K: Noise-based (Shifted 1 to use yesterday's data)
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k'] = df['noise'].rolling(window=period).mean().shift(1).clip(0.4, 0.6)
    
    # 2. Moving Average and ATR (Shifted 1 for breakout accuracy)
    df['ma'] = df['close'].rolling(window=period).mean().shift(1)
    tr = pd.concat([df['high'] - df['low'], 
                    abs(df['high'] - df['close'].shift(1)), 
                    abs(df['low'] - df['close'].shift(1))], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=period).mean().shift(1)
    
    # 3. Williams %R: Fixed 14-day window for consistency
    hh = df['high'].rolling(window=14).max()
    ll = df['low'].rolling(window=14).min()
    df['w_r'] = ((hh - df['close']) / (hh - ll) * -100).shift(1)
    
    # 4. Target Price and Volume Baseline
    df['target'] = df['open'] + (df['high'].shift(1) - df['low'].shift(1)) * df['k']
    df['vol_ma'] = df['volume'].rolling(window=period).mean().shift(1)
    
    return df

def run_backtest(df, vol_mult):
    """Strategy Engine: Trailing Stop, ATR SL, and Time Exit"""
    results = []
    
    # Entry: Breakout + Trend + Sentiment + Volume Threshold
    buy_cond = (df['open'] > df['ma']) & \
               (df['w_r'] < -20) & \
               (df['high'] > df['target']) & \
               (df['volume'] > df['vol_ma'] * vol_mult)
    
    trades = df[buy_cond].copy()
    
    for date, row in trades.iterrows():
        entry_price = row['target']
        stop_loss_price = entry_price - row['atr']
        
        # 1. Intra-day Trailing Stop simulation
        if row['high'] >= entry_price * PROFIT_TRIGGER:
            exit_price = row['high'] * (1 - TRAILING_CALLBACK)
            exit_price = max(exit_price, entry_price * (PROFIT_TRIGGER - 0.005))
        
        # 2. ATR Stop Loss
        elif row['low'] <= stop_loss_price:
            exit_price = stop_loss_price
            
        # 3. Time-based Exit: Sell at next day's open
        else:
            idx = df.index.get_loc(date)
            if idx + 1 < len(df):
                exit_price = df.iloc[idx + 1]['open']
            else: continue
                
        # Apply 0.2% penalty for fees and slippage
        yield_pct = ((exit_price / entry_price) * COST_PENALTY - 1) * 100
        results.append(yield_pct)
        
    win_rate = sum(1 for x in results if x > 0) / len(results) * 100 if results else 0
    return {"trades": len(results), "win_rate": round(win_rate, 2), "yield": round(sum(results), 2)}

@app.route("/")
def dashboard():
    summary_data = {}
    for ticker in TICKERS:
        df_raw = fetch_and_cache_data(ticker)
        if df_raw is None: continue
        
        results_by_thresh = {}
        for name, mult in VOL_THRESHOLDS.items():
            results_by_period = {}
            for p in PERIODS:
                df = calculate_indicators(df_raw.copy(), p).dropna()
                results_by_period[f"P{p}"] = run_backtest(df, mult)
            results_by_thresh[name] = results_by_period
        summary_data[ticker] = results_by_thresh

    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Quant V3.3 Final Logic Check</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; color: #333; margin: 0; padding: 40px; }
            .header { border-bottom: 2px solid #333; margin-bottom: 30px; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(450px, 1fr)); gap: 20px; }
            .card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            h3 { color: #2c3e50; border-left: 5px solid #2c3e50; padding-left: 10px; }
            .filter-label { font-size: 12px; color: #7f8c8d; font-weight: bold; margin-top: 15px; }
            table { width: 100%; border-collapse: collapse; margin-top: 5px; }
            th, td { text-align: center; padding: 12px; border-bottom: 1px solid #eee; font-size: 13px; }
            th { background-color: #f8f9fa; color: #95a5a6; }
            .pos { color: #27ae60; font-weight: bold; }
            .neg { color: #e74c3c; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Quant Strategy V3.3: Final Logic Verification</h1>
            <p>Sync: Variable K | Time Exit (09:00 AM) | Penalty (-0.2%) | 14d W%R Filter</p>
        </div>
        <div class="grid-container">
            {% for ticker, tests in summary.items() %}
            <div class="card">
                <h3>{{ ticker }}</h3>
                {% for t_name, periods in tests.items() %}
                <div class="filter-label">THRESHOLD: {{ t_name }}</div>
                <table>
                    <thead><tr><th>Period</th><th>Trades</th><th>Win Rate</th><th>Total Yield</th></tr></thead>
                    <tbody>
                    {% for p_name, data in periods.items() %}
                    <tr>
                        <td>{{ p_name }}</td>
                        <td>{{ data.trades }}</td>
                        <td>{{ data.win_rate }}%</td>
                        <td class="{% if data.yield > 0 %}pos{% else %}neg{% endif %}">{{ data.yield }}%</td>
                    </tr>
                    {% endfor %}
                    </tbody>
                </table>
                {% endfor %}
            </div>
            {% endfor %}
        </div>
    </body>
    </html>
    """
    return render_template_string(html_template, summary=summary_data)

if __name__ == "__main__":
    app.run(debug=False, port=5000)
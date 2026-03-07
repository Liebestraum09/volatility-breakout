import pyupbit
import pandas as pd
import numpy as np
import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

TICKERS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-DOT", "KRW-TRX", "KRW-LINK",
    "KRW-MATIC", "KRW-BCH", "KRW-SHIB", "KRW-LTC", "KRW-NEAR",
    "KRW-ATOM", "KRW-APT", "KRW-STX", "KRW-ETC", "KRW-SEI"
]
PERIODS = [7, 14, 21, 28]

# V3.5 Experiment: Long-term Moving Average Filter
LT_MA_FILTERS = {
    "No_LT_MA": 0,
    "MA_50": 50,
    "MA_100": 100
}

# Fixed verified logic from V3.3
VOL_MULTIPLIER = 1.0
PROFIT_TRIGGER = 1.02
TRAILING_CALLBACK = 0.01

def fetch_and_cache_data(ticker, days=730):
    cache_file = f"{ticker}_2yr_cache.pkl"
    if os.path.exists(cache_file): 
        return pd.read_pickle(cache_file)
    df = pyupbit.get_ohlcv(ticker, interval="day", count=days)
    if df is not None:
        df.to_pickle(cache_file)
        time.sleep(0.2)
    return df

def calculate_indicators(df, period, lt_ma_period):
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k'] = df['noise'].rolling(window=period).mean().shift(1).clip(0.4, 0.6)
    
    tr = pd.concat([df['high'] - df['low'], 
                    abs(df['high'] - df['close'].shift(1)), 
                    abs(df['low'] - df['close'].shift(1))], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=period).mean().shift(1)
    
    # Short-term MA for trend filter
    df['ma'] = df['close'].rolling(window=period).mean().shift(1)
    
    hh = df['high'].rolling(window=14).max()
    ll = df['low'].rolling(window=14).min()
    df['w_r'] = ((hh - df['close']) / (hh - ll) * -100).shift(1)
    
    df['target'] = df['open'] + (df['high'].shift(1) - df['low'].shift(1)) * df['k']
    df['vol_ma'] = df['volume'].rolling(window=period).mean().shift(1)
    
    # Long-term MA for macro trend filter
    if lt_ma_period > 0:
        df['lt_ma'] = df['close'].rolling(window=lt_ma_period).mean().shift(1)
    else:
        df['lt_ma'] = 0
        
    return df

def run_backtest(df, lt_ma_period):
    results = []
    
    # Base entry logic (V3.3)
    buy_cond = (df['open'] > df['ma']) & \
               (df['w_r'] < -20) & \
               (df['volume'] > df['vol_ma'] * VOL_MULTIPLIER) & \
               (df['high'] > df['target'])
               
    # Apply Long-term MA filter if specified
    if lt_ma_period > 0:
        buy_cond = buy_cond & (df['open'] > df['lt_ma'])
        
    trades = df[buy_cond].copy()
    
    for date, row in trades.iterrows():
        entry_price = row['target']
        stop_loss_price = entry_price - row['atr']
        
        if row['high'] >= entry_price * PROFIT_TRIGGER:
            exit_price = row['high'] * (1 - TRAILING_CALLBACK)
            exit_price = max(exit_price, entry_price * (PROFIT_TRIGGER - 0.005))
        elif row['low'] <= stop_loss_price:
            exit_price = stop_loss_price
        else:
            idx = df.index.get_loc(date)
            if idx + 1 < len(df):
                exit_price = df.iloc[idx + 1]['open']
            else: continue
                
        yield_pct = ((exit_price / entry_price) * 0.998 - 1) * 100
        results.append(yield_pct)
        
    win_rate = sum(1 for x in results if x > 0) / len(results) * 100 if results else 0
    return {"trades": len(results), "win_rate": round(win_rate, 2), "yield": round(sum(results), 2)}

@app.route("/")
def dashboard():
    summary_data = {}
    for ticker in TICKERS:
        df_raw = fetch_and_cache_data(ticker)
        if df_raw is None: continue
        
        results_by_filter = {}
        for name, lt_period in LT_MA_FILTERS.items():
            results_by_period = {}
            for p in PERIODS:
                df = calculate_indicators(df_raw.copy(), p, lt_period).dropna()
                results_by_period[f"P{p}"] = run_backtest(df, lt_period)
            results_by_filter[name] = results_by_period
        summary_data[ticker] = results_by_filter

    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Quant V5 Dashboard</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background-color: #FFFFFF; color: #333; margin: 0; padding: 40px; }
            h1 { border-bottom: 2px solid #F0F0F0; padding-bottom: 10px; font-weight: 600; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 25px; margin-top: 30px; }
            .card { border: 1px solid #E0E0E0; border-radius: 4px; padding: 25px; background: #FFF; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
            .card h3 { margin-top: 0; color: #1A1A1A; border-left: 4px solid #333; padding-left: 12px; font-size: 18px; }
            .filter-title { background-color: #F8F9FA; padding: 6px 12px; margin: 18px 0 6px 0; font-size: 13px; font-weight: bold; color: #555; }
            table { width: 100%; border-collapse: collapse; margin-bottom: 15px; }
            th, td { text-align: left; padding: 10px; border-bottom: 1px solid #F0F0F0; font-size: 13px; }
            th { color: #888; font-weight: normal; }
            .pos { color: #1E8449; font-weight: bold; }
            .neg { color: #A93226; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>Quantitative Strategy V3.5: Long-Term Trend Filter Test</h1>
        <p>Comparison: No Filter vs 50-day MA vs 100-day MA | Fixed: Volume 1.0x, Trailing Stop (2%/1%)</p>
        <div class="grid-container">
            {% for ticker, tests in summary.items() %}
            <div class="card">
                <h3>{{ ticker }}</h3>
                {% for t_name, periods in tests.items() %}
                <div class="filter-title">{{ t_name }}</div>
                <table>
                    <tr><th>Period</th><th>Trades</th><th>Win Rate</th><th>Total Yield</th></tr>
                    {% for p_name, data in periods.items() %}
                    <tr>
                        <td>{{ p_name }}</td>
                        <td>{{ data.trades }}</td>
                        <td>{{ data.win_rate }}%</td>
                        <td class="{% if data.yield > 0 %}pos{% else %}neg{% endif %}">{{ data.yield }}%</td>
                    </tr>
                    {% endfor %}
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
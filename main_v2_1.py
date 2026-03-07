import pyupbit
import pandas as pd
import numpy as np
import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# Expanded universe: Top 20 liquid assets on Upbit (Proxy for historical top volume)
TICKERS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-DOT", "KRW-TRX", "KRW-LINK",
    "KRW-MATIC", "KRW-BCH", "KRW-SHIB", "KRW-LTC", "KRW-NEAR",
    "KRW-ATOM", "KRW-APT", "KRW-STX", "KRW-ETC", "KRW-SEI"
]
PERIODS = [14, 21]

# Golden Ratio from V2.2 experiment
PROFIT_TRIGGER = 1.02  # 2% Profit
TRAILING_CALLBACK = 0.01 # 1% Callback

def fetch_and_cache_data(ticker, days=730):
    cache_file = f"{ticker}_2yr_cache.pkl"
    if os.path.exists(cache_file):
        return pd.read_pickle(cache_file)
    else:
        # Sequential fetch with delay to respect API rate limits
        df = pyupbit.get_ohlcv(ticker, interval="day", count=days)
        if df is not None:
            df.to_pickle(cache_file)
            time.sleep(0.2)
        return df

def calculate_indicators(df, period):
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k'] = df['noise'].rolling(window=period).mean().shift(1).clip(0.4, 0.6)
    
    tr = pd.concat([df['high'] - df['low'], 
                    abs(df['high'] - df['close'].shift(1)), 
                    abs(df['low'] - df['close'].shift(1))], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=period).mean().shift(1)
    df['ma'] = df['close'].rolling(window=period).mean().shift(1)
    
    hh = df['high'].rolling(window=14).max()
    ll = df['low'].rolling(window=14).min()
    df['w_r'] = ((hh - df['close']) / (hh - ll) * -100).shift(1)
    df['target'] = df['open'] + (df['high'].shift(1) - df['low'].shift(1)) * df['k']
    
    return df

def run_backtest_v2_2(df):
    results = []
    buy_cond = (df['open'] > df['ma']) & (df['w_r'] < -20) & (df['high'] > df['target'])
    trades = df[buy_cond].copy()
    
    for date, row in trades.iterrows():
        entry_price = row['target']
        stop_loss_price = entry_price - row['atr']
        
        # Golden Ratio Trailing Stop Logic
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
    total_yield = sum(results)
    
    return {
        "trades": len(results),
        "win_rate": round(win_rate, 2),
        "total_yield": round(total_yield, 2)
    }

@app.route("/")
def dashboard():
    summary_data = {}
    for ticker in TICKERS:
        df_raw = fetch_and_cache_data(ticker)
        if df_raw is None: continue
        
        ticker_results = {}
        for p in PERIODS:
            df = calculate_indicators(df_raw.copy(), period=p).dropna()
            ticker_results[f"Period_{p}"] = run_backtest_v2_2(df)
        summary_data[ticker] = ticker_results

    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Quant V2.2 Extended Dashboard</title>
        <style>
            body { font-family: 'Arial', sans-serif; background-color: #FFFFFF; color: #333; margin: 0; padding: 40px; }
            h1 { border-bottom: 2px solid #F0F0F0; padding-bottom: 10px; font-weight: 600; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-top: 25px; }
            .card { border: 1px solid #E0E0E0; border-radius: 4px; padding: 20px; background: #FFF; }
            .card h3 { margin-top: 0; color: #1A1A1A; border-left: 4px solid #333; padding-left: 8px; font-size: 16px; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { text-align: left; padding: 10px; border-bottom: 1px solid #F0F0F0; font-size: 12px; }
            th { background-color: #FAFAFA; color: #666; }
            .positive { color: #1E8449; font-weight: bold; }
            .negative { color: #A93226; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>Quantitative Strategy V2.2 (Top 20 Universe)</h1>
        <p>Fixed Strategy: Trailing Stop (T:2%, C:1%) | Periods: 14d, 21d | Data: 2-Year History</p>
        
        <div class="grid-container">
            {% for ticker, periods in summary.items() %}
            <div class="card">
                <h3>{{ ticker }}</h3>
                <table>
                    <tr>
                        <th>Period</th>
                        <th>Trades</th>
                        <th>Win Rate</th>
                        <th>Total Yield</th>
                    </tr>
                    {% for period, data in periods.items() %}
                    <tr>
                        <td>{{ period }}</td>
                        <td>{{ data.trades }}</td>
                        <td>{{ data.win_rate }}%</td>
                        <td class="{% if data.total_yield > 0 %}positive{% elif data.total_yield < 0 %}negative{% endif %}">
                            {{ data.total_yield }}%
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
            {% endfor %}
        </div>
    </body>
    </html>
    """
    return render_template_string(html_template, summary=summary_data)

if __name__ == "__main__":
    app.run(debug=False, port=5000)
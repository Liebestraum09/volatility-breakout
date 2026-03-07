import pyupbit
import pandas as pd
import numpy as np
import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# 테스트할 유동성 상위 고정 유니버스
TICKERS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
PERIODS = [7, 14, 21, 30]

def fetch_and_cache_data(ticker, days=730):
    cache_file = f"{ticker}_2yr_cache.pkl"
    if os.path.exists(cache_file):
        # 캐시가 존재하면 파일에서 로드 (초고속)
        return pd.read_pickle(cache_file)
    else:
        # 캐시가 없으면 API 호출 후 저장
        df = pyupbit.get_ohlcv(ticker, interval="day", count=days)
        if df is not None:
            df.to_pickle(cache_file)
            time.sleep(0.5) # API 호출 제한 방지
        return df

def calculate_indicators(df, period):
    # 1. Dynamic K (Noise Ratio Mean)
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k'] = df['noise'].rolling(window=period).mean().shift(1).clip(0.4, 0.6)
    
    # 2. ATR (Average True Range)
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - df['close'].shift(1))
    tr3 = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=period).mean().shift(1)
    
    # 3. MA (Moving Average)
    df['ma'] = df['close'].rolling(window=period).mean().shift(1)
    
    # 4. Williams %R (Fixed 14 days)
    hh = df['high'].rolling(window=14).max()
    ll = df['low'].rolling(window=14).min()
    df['w_r'] = ((hh - df['close']) / (hh - ll) * -100).shift(1)
    
    # [참고용] Larry Williams Extra Indicators (Not used in execution)
    df['blast_off'] = (abs(df['open'] - df['close']) / (df['high'] - df['low'])).shift(1)
    df['target'] = df['open'] + (df['high'].shift(1) - df['low'].shift(1)) * df['k']
    
    return df

def run_backtest(df):
    results = []
    # 매수 조건: 시가가 MA 위 & 전일 %R이 과매수(-20)가 아닐 때 & 고가가 타점을 돌파
    buy_cond = (df['open'] > df['ma']) & (df['w_r'] < -20) & (df['high'] > df['target'])
    
    trades = df[buy_cond].copy()
    
    for date, row in trades.iterrows():
        entry_price = row['target']
        stop_loss_price = entry_price - row['atr']
        
        # 당일 저가가 손절선 밑으로 내려갔다면 ATR 손절
        if row['low'] <= stop_loss_price:
            exit_price = stop_loss_price
            status = "Stop Loss (ATR)"
        else:
            # 안전하게 다음날 시가에 매도 (데이터가 존재하는지 확인)
            idx = df.index.get_loc(date)
            if idx + 1 < len(df):
                exit_price = df.iloc[idx + 1]['open']
                status = "Next Open Settle"
            else:
                continue
                
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
    extra_indicators_data = {}
    
    for ticker in TICKERS:
        df_raw = fetch_and_cache_data(ticker)
        if df_raw is None:
            continue
            
        ticker_results = {}
        for p in PERIODS:
            df = df_raw.copy()
            df = calculate_indicators(df, period=p)
            ticker_results[f"Period_{p}"] = run_backtest(df.dropna())
            
            # 마지막 날의 참고용 지표 저장 (프론트엔드 표시용)
            if p == 14:
                last_row = df.iloc[-1]
                extra_indicators_data[ticker] = {
                    "Blast_Off": round(last_row['blast_off'], 3),
                    "W_R": round(last_row['w_r'], 2),
                    "ATR": round(last_row['atr'], 2)
                }
                
        summary_data[ticker] = ticker_results

    # HTML 템플릿 (깔끔한 흰색 바탕, 구역 분할)
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Quant V2 Dashboard</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #FFFFFF; color: #333; margin: 0; padding: 40px; }
            h1, h2 { border-bottom: 2px solid #EAEAEA; padding-bottom: 10px; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; margin-top: 20px; }
            .card { border: 1px solid #EAEAEA; border-radius: 8px; padding: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
            .card h3 { margin-top: 0; color: #2C3E50; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { text-align: left; padding: 8px; border-bottom: 1px solid #EAEAEA; font-size: 14px; }
            th { background-color: #F8F9FA; color: #555; }
            .positive { color: #27AE60; font-weight: bold; }
            .negative { color: #C0392B; font-weight: bold; }
            .indicator-section { background-color: #F8F9FA; padding: 20px; border-radius: 8px; margin-top: 40px; }
        </style>
    </head>
    <body>
        <h1>Quantitative Strategy V2 Backtest (2 Years)</h1>
        <p>Target: Top 5 Liquid Assets | Logic: Dynamic K + ATR Stop Loss + MA/WR Filters</p>
        
        <h2>Performance by Lookback Period</h2>
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

        <div class="indicator-section">
            <h2>Current Market Indicators (Reference Only)</h2>
            <p>Larry Williams secondary indicators calculated from the most recent daily close.</p>
            <table>
                <tr>
                    <th>Ticker</th>
                    <th>Blast Off Indicator (Expansion Risk)</th>
                    <th>Williams %R (Overbought/Oversold)</th>
                    <th>ATR (Current Volatility)</th>
                </tr>
                {% for ticker, ind in extra.items() %}
                <tr>
                    <td><strong>{{ ticker }}</strong></td>
                    <td>{{ ind.Blast_Off }}</td>
                    <td>{{ ind.W_R }}</td>
                    <td>{{ ind.ATR }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """
    return render_template_string(html_template, summary=summary_data, extra=extra_indicators_data)

if __name__ == "__main__":
    print("Starting backtest engine and web server...")
    print("Open your browser and navigate to: http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
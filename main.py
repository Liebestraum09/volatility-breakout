import pyupbit
import sqlite3
import time
from datetime import datetime
import pandas as pd
import numpy as np

# --- [1] 종목별 최적 파라미터 설정 (백테스트 결과 반영) ---
CONFIG = {
    'KRW-KAITO': {'K_BASE': 0.40, 'MA': 5,  'SL': 0.02},
    'KRW-PUNDIX':{'K_BASE': 0.40, 'MA': 20, 'SL': 0.02},
    'KRW-LSK':   {'K_BASE': 0.40, 'MA': 5,  'SL': 0.02},
    'KRW-BONK':  {'K_BASE': 0.60, 'MA': 5,  'SL': 0.02},
    'KRW-SEI':   {'K_BASE': 0.45, 'MA': 5,  'SL': 0.02}
}

# --- [2] DB 및 보조 함수 ---
def init_db():
    conn = sqlite3.connect("trading_log.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, entry_time TEXT, entry_price REAL, target_price REAL, status TEXT
        )
    """)
    conn.commit()
    conn.close()

def record_trade(ticker, target_price, current_price):
    conn = sqlite3.connect("trading_log.db")
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT * FROM trades WHERE ticker = ? AND entry_time LIKE ?", (ticker, today + '%'))
    
    if cursor.fetchone() is None:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT INTO trades (ticker, entry_time, entry_price, target_price, status) VALUES (?, ?, ?, ?, ?)",
                       (ticker, now, current_price, target_price, 'OPEN'))
        print(f" [LOG] Virtual BUY: {ticker} at {current_price:,.0f}")
    conn.commit()
    conn.close()

def get_signals(ticker, config):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=30)
        if df is None or len(df) < 20: return None, None, None
        
        # 1. 가변 K (노이즈 20일 평균) 계산 및 0.4~0.6 제한
        df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
        avg_noise = df['noise'].rolling(window=20).mean().iloc[-2]
        current_k = np.clip(avg_noise + (config['K_BASE'] - 0.5), 0.4, 0.6)
        
        # 2. 타점 및 이평선 필터
        target = df.iloc[-1]['open'] + (df.iloc[-2]['high'] - df.iloc[-2]['low']) * current_k
        ma = df['close'].rolling(window=config['MA']).mean().iloc[-2]
        
        # 3. Williams %R 필터 (14일)
        high_h = df['high'].iloc[-14:].max()
        low_l = df['low'].iloc[-14:].min()
        curr_price = pyupbit.get_current_price(ticker)
        w_r = (high_h - curr_price) / (high_h - low_l) * -100
        
        return target, ma, w_r
    except: return None, None, None

# --- [3] 메인 루프 ---
def run_engine():
    print(f"🚀 QUANT ENGINE STARTING... (Targets: {list(CONFIG.keys())})")
    init_db()
    
    while True:
        try:
            now = datetime.now().strftime('%H:%M:%S')
            for ticker, cfg in CONFIG.items():
                target, ma, w_r = get_signals(ticker, cfg)
                current = pyupbit.get_current_price(ticker)
                
                if None in [target, ma, w_r, current]: continue
                
                # 매수 조건: 돌파 & 이평선 위 & 과매수 아님(%R < -20)
                if (current >= target) and (current > ma) and (w_r < -20):
                    record_trade(ticker, target, current)
                
                print(f"[{now}] {ticker:<12} | Target: {target:>10,.0f} | Cur: {current:>10,.0f} | %R: {w_r:>5.1f}")
                time.sleep(0.2)
            
            time.sleep(60) # 1분마다 스캔
        except Exception as e:
            print(f"Error: {e}"); time.sleep(10)

if __name__ == "__main__":
    run_engine()
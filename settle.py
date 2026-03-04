import pyupbit
import sqlite3
from datetime import datetime

def settle_trades():
    conn = sqlite3.connect("trading_log.db")
    cursor = conn.cursor()
    
    # 정산 대상(OPEN) 가져오기
    cursor.execute("SELECT id, ticker, entry_price FROM trades WHERE status = 'OPEN'")
    open_trades = cursor.fetchall()
    
    print(f"\n{'Ticker':<12} | {'Entry':>10} | {'Exit (Open)':>12} | {'Return'}")
    print("-" * 55)
    
    for trade in open_trades:
        tid, ticker, entry_price = trade
        
        # 오늘 아침 9시 시가(Open) 가져오기
        df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
        if df is not None:
            exit_price = df.iloc[0]['open']
            profit_rate = ((exit_price - entry_price) / entry_price) * 100
            
            # DB 업데이트 및 CLOSED 처리
            cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE id = ?", (tid,))
            print(f"{ticker:<12} | {entry_price:>10,.1f} | {exit_price:>12,.1f} | {profit_rate:>6.2f}%")
            
    conn.commit()
    conn.close()
    print("-" * 55)

if __name__ == "__main__":
    settle_trades()
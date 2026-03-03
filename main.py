import pyupbit
import pandas as pd
from datetime import datetime
import time

def get_target_price(ticker):
    """Calculate the target price for a given ticker."""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
        if df is None or len(df) < 2:
            return None
        
        yesterday = df.iloc[0]
        today = df.iloc[1]
        
        # Volatility Range = Yesterday's High - Yesterday's Low
        target_price = today['open'] + (yesterday['high'] - yesterday['low']) * 0.5
        return target_price
    except Exception as e:
        print(f"Error calculating target for {ticker}: {e}")
        return None

def scan_market():
    # Fetch top 10 tickers by trading volume (proxy for market cap/liquidity in KRW market)
    tickers = pyupbit.get_tickers(fiat="KRW")[:10]
    
    # Get current timestamp for the dashboard
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"\n" + "="*55)
    print(f" CRYPTO MONITORING DASHBOARD | {now}")
    print("="*55)
    print(f"{'Ticker':<12} | {'Target':>12} | {'Current':>12} | {'Status'}")
    print("-"*55)

    for ticker in tickers:
        target = get_target_price(ticker)
        current = pyupbit.get_current_price(ticker)
        
        if target is None or current is None:
            continue

        status = "🟢 [BUY]" if current >= target else "⚪ [WAIT]"
        
        # Display formatted results
        print(f"{ticker:<12} | {target:>12,.0f} | {current:>12,.0f} | {status}")
        
        # Optional: Sleep briefly to avoid API rate limits
        time.sleep(0.05)
    
    print("="*55)

if __name__ == "__main__":
    scan_market()
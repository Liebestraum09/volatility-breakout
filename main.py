import pyupbit
import pandas as pd

def calculate_target_price(ticker):
    """
    Calculates the target price based on the Volatility Breakout Strategy.
    Target Price = Today's Open + (Yesterday's High - Yesterday's Low) * K
    """
    # Fetch Daily OHLCV (Open, High, Low, Close, Volume) data
    # count=2 retrieves data for yesterday and today

    df = pyupbit.get_ohlcv(ticker, interval="day", count=2)

    if df is None or len(df) < 2:
        print(f"Error: Could not retrieve data for {ticker}")
        return None
    
    yesterday = df.iloc[0]
    today = df.iloc[1]

    # Calculate range ( Valotility of th eprevious day)
    # Range = High - Low
    prev_range = yesterday['high'] - yesterday['low']

    # K-value is usually set to 0.5 as a standard
    k = 0.5
    target_price = today['open'] + (prev_range * k)
    
    return target_price

def monitor_market():
    ticker = "KRW-BTC"
    target_price = calculate_target_price(ticker)
    current_price = pyupbit.get_current_price(ticker)

    if target_price is None or current_price is None:
        return

    print("=" * 40)
    print(f" Market Monitoring: {ticker}")
    print("=" * 40)
    print(f" Target Price  : {target_price:,.0f} KRW")
    print(f" Current Price : {current_price:,.0f} KRW")
    print("-" * 40)

    if current_price >= target_price:
        print(" Status: [SIGNAL] Breakout detected! Consider Buying.")
    else:
        gap = target_price - current_price
        print(f" Status: [WAIT] {gap:,.0f} KRW below target.")
    print("=" * 40)

if __name__ == "__main__":
    monitor_market()
import pyupbit
import pandas as pd
import numpy as np
import time
from tqdm import tqdm

# 현실적 변수 및 보수적 페널티 설정
BASIC_COST = 0.002  # 왕복 수수료(0.1%) + 슬리피지(0.1%)

def get_optimized_backtest(df, k_base, ma_period, stop_loss, psych_cost):
    try:
        temp_df = df.copy()
        
        # 1. 지표 계산: 노이즈 비율(Variable K)
        temp_df['noise'] = 1 - abs(temp_df['open'] - temp_df['close']) / (temp_df['high'] - temp_df['low'])
        # 20일 평균 노이즈를 K로 사용하되, k_base를 중심으로 0.4~0.6 범위 제한
        temp_df['k'] = temp_df['noise'].rolling(window=20).mean().shift(1)
        temp_df['k'] = np.clip(temp_df['k'] + (k_base - 0.5), 0.4, 0.6)
        
        # 2. 지표 계산: Williams %R (14일)
        lookback = 14
        high_h = temp_df['high'].rolling(window=lookback).max()
        low_l = temp_df['low'].rolling(window=lookback).min()
        temp_df['w_r'] = (high_h - temp_df['close']) / (high_h - low_l) * -100
        
        # 3. 이평선 및 타점
        temp_df['ma'] = temp_df['close'].rolling(window=ma_period).mean()
        temp_df['range'] = (temp_df['high'].shift(1) - temp_df['low'].shift(1))
        temp_df['target'] = temp_df['open'] + temp_df['range'] * temp_df['k']
        
        # 4. 매수 조건: (타점 돌파) & (이평선 위) & (%R 필터: 과매수 -20 이상 진입 금지)
        temp_df['is_buy'] = (temp_df['high'] > temp_df['target']) & \
                            (temp_df['open'] > temp_df['ma'].shift(1)) & \
                            (temp_df['w_r'].shift(1) < -20)
        
        # 5. 수익률 계산 (기본 비용 + 심리적 비용 페널티 합산)
        total_penalty = BASIC_COST + psych_cost
        temp_df['ror'] = 1.0
        buy_indices = temp_df[temp_df['is_buy']].index
        
        if not buy_indices.empty:
            raw_ror = (temp_df.loc[buy_indices, 'close'] / temp_df.loc[buy_indices, 'target']) * (1 - total_penalty)
            # 손절 로직 반영
            temp_df.loc[buy_indices, 'ror'] = np.maximum(raw_ror, 1 - stop_loss)
                
        return temp_df['ror'].iloc[-365:].cumprod().iloc[-1]
    except:
        return 0

# 하이퍼파라미터 조합 (0.05 단위 세분화 및 페널티 추가)
k_base_list = np.arange(0.4, 0.65, 0.05)       # 0.4 ~ 0.6 (0.05 단위)
ma_list = [5, 10, 20]
sl_list = [0.02, 0.03, 0.05]
psych_list = [0.001, 0.002, 0.003]            # 심리적 비용 (0.1% ~ 0.3%)
tickers = pyupbit.get_tickers(fiat="KRW")[:50] 

# 1. 데이터 캐싱
print("📦 Caching historical data...")
cache = {}
for ticker in tqdm(tickers, desc="Downloading"):
    df = pyupbit.get_ohlcv(ticker, interval="day", count=400)
    if df is not None: cache[ticker] = df
    time.sleep(0.1)

# 2. 전수 조사 (Grid Search)
total_combos = len(cache) * len(k_base_list) * len(ma_list) * len(sl_list) * len(psych_list)
print(f"\n⚡ Optimizing {total_combos} combinations with %R Filter & Variable K...")
results = []

with tqdm(total=total_combos, desc="Computing") as pbar:
    for ticker, df in cache.items():
        best_hpr = 0
        best_params = {}
        for k_b in k_base_list:
            for ma in ma_list:
                for sl in sl_list:
                    for psych in psych_list:
                        hpr = get_optimized_backtest(df, k_b, ma, sl, psych)
                        if hpr > best_hpr:
                            best_hpr = hpr
                            best_params = {'K_Base': round(k_b, 2), 'MA': ma, 'SL': sl, 'Psych': psych}
                        pbar.update(1)
        results.append({'ticker': ticker, 'hpr': best_hpr, 'params': best_params})

# 결과 출력
results.sort(key=lambda x: x['hpr'], reverse=True)
print("\n" + "="*80)
print(f"{'Rank':<5} | {'Ticker':<12} | {'Annual Return':>15} | {'Best Parameters'}")
print("-" * 80)
for i, res in enumerate(results[:15], 1):
    p = res['params']
    param_str = f"K:{p['K_Base']}, MA:{p['MA']}, SL:{p['SL']}, Psych:{p['Psych']}"
    print(f"{i:<5} | {res['ticker']:<12} | {res['hpr']:>14.2f}x | {param_str}")
print("="*80)
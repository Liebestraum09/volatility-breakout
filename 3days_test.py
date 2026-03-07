import pyupbit
import pandas as pd
import numpy as np

# 우리가 백테스트로 찾아낸 정예 5인방 최적 파라미터
CONFIG = {
    'KRW-KAITO': {'K_BASE': 0.40, 'MA': 5,  'SL': 0.02},
    'KRW-PUNDIX':{'K_BASE': 0.40, 'MA': 20, 'SL': 0.02},
    'KRW-LSK':   {'K_BASE': 0.40, 'MA': 5,  'SL': 0.02},
    'KRW-BONK':  {'K_BASE': 0.60, 'MA': 5,  'SL': 0.02},
    'KRW-SEI':   {'K_BASE': 0.45, 'MA': 5,  'SL': 0.02}
}

print("📊 지난 3일간(3월 4일~6일)의 실전 시뮬레이션 결과를 불러옵니다...\n" + "="*50)

for ticker, cfg in CONFIG.items():
    # 지표 계산을 위해 넉넉히 30일치 데이터 호출
    df = pyupbit.get_ohlcv(ticker, interval="day", count=30)
    if df is None or len(df) < 20:
        continue
        
    # 1. 가변 K 계산 (20일 노이즈 평균)
    df['noise'] = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['k'] = df['noise'].rolling(window=20).mean().shift(1)
    df['k'] = np.clip(df['k'] + (cfg['K_BASE'] - 0.5), 0.4, 0.6)
    
    # 2. 이동평균선(MA)
    df['ma'] = df['close'].rolling(window=cfg['MA']).mean().shift(1)
    
    # 3. Williams %R (14일)
    high_h = df['high'].rolling(window=14).max()
    low_l = df['low'].rolling(window=14).min()
    df['w_r'] = (high_h - df['close']) / (high_h - low_l) * -100
    
    # 4. 타점 및 진입 조건
    df['range'] = df['high'].shift(1) - df['low'].shift(1)
    df['target'] = df['open'] + df['range'] * df['k']
    
    # 매수 조건: 타점 돌파 & 이평선 위 & 과매수(-20) 아님
    df['is_buy'] = (df['high'] > df['target']) & (df['open'] > df['ma']) & (df['w_r'].shift(1) < -20)
    
    # 최근 4일치 데이터만 추출 (오늘 포함, 지난 3일 완결된 봉 확인)
    recent_df = df.iloc[-4:]
    trade_found = False
    
    for i in range(len(recent_df) - 1):
        date = recent_df.index[i]
        next_date = recent_df.index[i+1]
        row = recent_df.iloc[i]
        next_row = recent_df.iloc[i+1]
        
        if row['is_buy']:
            trade_found = True
            entry_price = row['target']
            
            # 당일 저가가 손절선을 건드렸는지 확인
            if row['low'] <= entry_price * (1 - cfg['SL']):
                exit_price = entry_price * (1 - cfg['SL'])
                status = "손절 (Stop Loss -2%)"
            else:
                exit_price = next_row['open']
                status = "익일 아침 9시 매도"
            
            # 수수료 및 슬리피지 보수적으로 0.2% 차감
            profit_pct = ((exit_price / entry_price) * 0.998 - 1) * 100
            
            print(f"[{date.strftime('%Y-%m-%d')}] {ticker}")
            print(f"  - 진입가: {entry_price:,.2f}원 | 매도가: {exit_price:,.2f}원 ({status})")
            print(f"  - 최종 수익률: {profit_pct:.2f}%")
    
    if not trade_found:
        print(f"[{ticker}] 진입 조건 불만족 (필터 방어 성공 또는 타점 미달)")
    print("-" * 50)
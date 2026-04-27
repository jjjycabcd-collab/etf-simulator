import sys
import pandas as pd
import re
import json
import datetime
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정 및 상태 초기화
# ==========================================
st.set_page_config(page_title="국내 주식 시뮬레이터", layout="wide")

if 'show_settings' not in st.session_state:
    st.session_state.show_settings = True
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False
if 'sim_result_data' not in st.session_state:
    st.session_state.sim_result_data = None
if 'display_title' not in st.session_state:
    st.session_state.display_title = ""

# ==========================================
# 함수 정의부
# ==========================================

@st.cache_data(ttl=86400)
def get_stock_info(code):
    """야후 파이낸스에서 국내 종목명 가져오기 (.KS, .KQ 자동 처리)"""
    if not code: return ""
    try:
        # 6자리 숫자 코드인 경우 코스피(.KS)를 먼저 시도
        check_code = f"{code}.KS" if code.isdigit() else code
        ticker = yf.Ticker(check_code)
        name = ticker.info.get('shortName', None)
        
        # 코스피에서 찾지 못한 경우 코스닥(.KQ)으로 재시도
        if name is None and code.isdigit():
            check_code = f"{code}.KQ"
            ticker = yf.Ticker(check_code)
            name = ticker.info.get('shortName', code)
            
        return f"{name}({code.upper()})"
    except:
        return code.upper()

def fetch_prices(code, start_date, end_date):
    """야후 파이낸스 가격 데이터 수집 (국내 시장)"""
    try:
        if code.isdigit():
            # 코스피 시도
            ticker = yf.Ticker(f"{code}.KS")
            df = ticker.history(start=start_date, end=end_date)
            if df.empty:
                # 코스닥 시도
                ticker = yf.Ticker(f"{code}.KQ")
                df = ticker.history(start=start_date, end=end_date)
        else:
            ticker = yf.Ticker(code)
            df = ticker.history(start=start_date, end=end_date)
            
        if df.empty: return pd.Series(dtype=float)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close']
    except:
        return pd.Series(dtype=float)

# ==========================================
# UI 영역
# ==========================================
st.title("🇰🇷 국내 주식/ETF 시뮬레이터")

if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 시뮬레이션 설정 다시 하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 시뮬레이션 설정")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 총 투자금 (원)", "10,000,000")
            period_input = st.text_input("백테스트 기간 (예: 2023~2024)", "2023~2024")

        with col2:
            etf_input = st.text_input("종목 코드 (숫자 6자리, 쉼표 구분)", "005930, 000660")
            strategy_options = st.multiselect(
                "분할 매수 방식 (※ 단일 종목 입력 시에만 비교 적용)",
                ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                default=["거치식 (일괄 매수)", "적립식 (매월)"]
            )
            
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('국내 시장 데이터 분석 중...'):
            try:
                INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            except:
                INITIAL_CASH = 10000000.0
            
            try:
                if '~' in period_input:
                    s_str, e_str = period_input.split('~')
                    start_dt = pd.to_datetime(s_str.strip() if '.' in s_str else f"{s_str.strip()}-01-01")
                    end_dt = pd.to_datetime(e_str.strip() if '.' in e_str else f"{e_str.strip()}-12-31")
                else:
                    start_dt = pd.to_datetime(f"{period_input.strip()}-01-01")
                    end_dt = pd.to_datetime(f"{period_input.strip()}-12-31")
            except:
                start_dt, end_dt = pd.to_datetime("2023-01-01"), pd.to_datetime("2023-12-31")

            tickers = [t.strip().upper() for t in etf_input.replace(',', ' ').split() if t.strip()]
            if not tickers: tickers = ["005930"]

            targets = []
            if len(tickers) == 1:
                if not strategy_options:
                    strategy_options = ["적립식 (매월)"]
                    
                compare_keys = strategy_options
                for strat in strategy_options:
                    targets.append({'key': strat, 'ticker': tickers[0], 'strategy': strat, 'name': f"{get_stock_info(tickers[0])} - {strat}"})
                st.session_state.display_title = f"### 📊 {period_input} {get_stock_info(tickers[0])} 투자 방식 비교"
            else:
                compare_keys = tickers
                for t in tickers:
                    targets.append({'key': t, 'ticker': t, 'strategy': "거치식 (일괄 매수)", 'name': get_stock_info(t)})
                st.session_state.display_title = f"### 📊 {period_input} 국내 종목 비교 (거치식)"

            all_sim_data = {}
            chart_labels = []
            
            for target in targets:
                t_key = target['key']
                t_code = target['ticker']
                strat = target['strategy']
                name = target['name']
                
                prices = fetch_prices(t_code, start_dt, end_dt)
                if prices.empty: continue
                
                if strat == "거치식 (일괄 매수)":
                    invest_dates = [prices.index[0]]
                elif strat == "적립식 (매일)":
                    invest_dates = prices.index
                elif strat == "적립식 (매주)":
                    invest_dates = prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week]).head(1).index
                elif strat == "적립식 (매월)":
                    invest_dates = prices.groupby([prices.index.year, prices.index.month]).head(1).index
                else:
                    invest_dates = [prices.index[0]]

                N_invest = len(invest_dates)
                installment = INITIAL_CASH / N_invest if N_invest > 0 else 0
                invest_dates_set = set(invest_dates)
                
                reserve_cash = INITIAL_CASH 
                available_cash = 0.0        
                total_shares = 0
                
                history = []
                summary = []
                ticker_chart_values = []
                prev_asset = INITIAL_CASH
                
                # 주별(Weekly) 그룹핑
                weekly_groups = prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week])
                
                for (y, w), group in weekly_groups:
                    eow_dt = group.index[-1]
                    eow_price = float(group.iloc[-1])
                    
                    for date, price in group.items():
                        if date in invest_dates_set:
                            reserve_cash -= installment
                            available_cash += installment
                            
                            shares_to_buy = int(available_cash // float(price))
                            if shares_to_buy > 0:
                                available_cash -= shares_to_buy * float(price)
                                total_shares += shares_to_buy
                                
                                history.append({
                                    '날짜': date.strftime('%Y/%m/%d'),
                                    '구분': '매수' if strat == "거치식 (일괄 매수)" else '분할매수',
                                    '단가': float(price),
                                    '수량': shares_to_buy,
                                    '거래금액': float(shares_to_buy * price),
                                    '현금잔고': float(reserve_cash + available_cash), 
                                    '총자산': float(reserve_cash + available_cash + (total_shares * price))
                                })
                    
                    current_asset = float(reserve_cash + available_cash + (total_shares * eow_price))
                    
                    label = eow_dt.strftime('%Y/%m/%d')
                    
                    if label not in chart_labels: chart_labels.append(label)
                    ticker_chart_values.append(current_asset)
                    
                    summary.append({
                        '기간': label,
                        '기말단가': eow_price,
                        '기말자산': current_asset,
                        '증감': float(current_asset - prev_asset),
                        '수익률': float(((current_asset / INITIAL_CASH) - 1) * 100)
                    })
                    
                    history.append({
                        '날짜': eow_dt.strftime('%Y/%m/%d'),
                        '구분': '평가',
                        '단가': eow_price,
                        '수량': int(total_shares),
                        '거래금액': 0.0,
                        '현금잔고': float(reserve_cash + available_cash),
                        '총자산': current_asset
                    })
                    prev_asset = current_asset
                
                all_sim_data[t_key] = {
                    'name': name,
                    'summary': summary,
                    'history': history,
                    'chart_values': ticker_chart_values,
                    'final_asset': prev_asset,
                    'total_profit': prev_asset - INITIAL_CASH,
                    'profit_rate': ((prev_asset / INITIAL_CASH) - 1) * 100
                }

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH,
                'compare_keys': compare_keys,
                'labels': chart_labels,
                'all_data': all_sim_data
            }
            st.session_state.run_clicked = True
            st.session_state.show_settings = False
            st.rerun()

# ==========================================
# 결과 출력 영역 (Client-Side Rendering)
# ==========================================
if st.session_state.run_clicked and st.session_state.sim_result_data:
    res = st.session_state.sim_result_data
    st.markdown(st.session_state.display_title)

    datasets = []
    colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4']
    for idx, t_key in enumerate(res['compare_keys']):
        if t_key in res['all_data']:
            t_data = res['all_data'][t_key]
            datasets.append({
                'label': t_data['name'],
                'data': t_data['chart_values'],
                'borderColor': colors[idx % len(colors)],
                'tension': 0.4, 
                'fill': False
            })

    html_code = f"""
    <!DOCTYPE html><html><head><meta charset="utf-

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
st.set_page_config(page_title="국내 주식 배당 재투자 시뮬레이터", layout="wide")

if 'show_settings' not in st.session_state:
    st.session_state.show_settings = True
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False
if 'sim_result_data' not in st.session_state:
    st.session_state.sim_result_data = None

# ==========================================
# 함수 정의부
# ==========================================

@st.cache_data(ttl=86400)
def get_stock_info(code):
    """종목명 가져오기"""
    if not code: return ""
    try:
        check_code = f"{code}.KS" if code.isdigit() else code
        ticker = yf.Ticker(check_code)
        name = ticker.info.get('shortName', None)
        if name is None and code.isdigit():
            check_code = f"{code}.KQ"
            ticker = yf.Ticker(check_code)
            name = ticker.info.get('shortName', code)
        return f"{name}({code.upper()})"
    except:
        return code.upper()

def fetch_prices_and_dividends(code, start_date, end_date):
    """가격 및 배당 데이터 수집"""
    try:
        ticker_code = f"{code}.KS" if code.isdigit() else code
        ticker = yf.Ticker(ticker_code)
        df = ticker.history(start=start_date, end=end_date)
        
        # 코스피에 없으면 코스닥 시도
        if df.empty and code.isdigit():
            ticker_code = f"{code}.KQ"
            ticker = yf.Ticker(ticker_code)
            df = ticker.history(start=start_date, end=end_date)
            
        if df.empty: 
            return pd.Series(dtype=float), pd.Series(dtype=float)
            
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close'].dropna(), df['Dividends'].replace(0, pd.NA).dropna()
    except:
        return pd.Series(dtype=float), pd.Series(dtype=float)

# ==========================================
# UI 영역
# ==========================================
st.title("🇰🇷 국내 주식 배당 재투자 시뮬레이터")

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
            period_input = st.text_input("백테스트 기간", "2025~2026")

        with col2:
            etf_input = st.text_input("종목 코드 (최대 4개)", "498400, 472150, 475720")
            strategy_options = st.multiselect(
                "분할 매수 방식 (단일 종목 시 적용)",
                ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                default=["거치식 (일괄 매수)", "적립식 (매월)"]
            )
            
        run_btn = st.button("🚀 배당 재투자 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('배당 및 주가 데이터를 분석 중...'):
            INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            try:
                if '~' in period_input:
                    s_str, e_str = period_input.split('~')
                    start_dt = pd.to_datetime(s_str.strip() if '.' in s_str else f"{s_str.strip()}-01-01")
                    end_dt = pd.to_datetime(e_str.strip() if '.' in e_str else f"{e_str.strip()}-12-31")
                else:
                    start_dt = pd.to_datetime(f"{period_input.strip()}-01-01")
                    end_dt = pd.to_datetime(f"{period_input.strip()}-12-31")
            except:
                start_dt, end_dt = pd.to_datetime("2025-01-01"), pd.to_datetime("2026-12-31")

            tickers = [t.strip().upper() for t in etf_input.replace(',', ' ').split() if t.strip()][:4]
            targets = []
            if len(tickers) == 1:
                compare_keys = strategy_options if strategy_options else ["적립식 (매월)"]
                for strat in compare_keys:
                    targets.append({'key': strat, 'ticker': tickers[0], 'strategy': strat, 'name': f"{get_stock_info(tickers[0])} ({strat})"})
            else:
                compare_keys = tickers
                for t in tickers:
                    targets.append({'key': t, 'ticker': t, 'strategy': "거치식 (일괄 매수)", 'name': get_stock_info(t)})

            all_sim_data = {}
            global_dates = set()
            
            # 데이터 수집 및 공통 날짜 생성
            target_raw_data = {}
            for target in targets:
                prices, divs = fetch_prices_and_dividends(target['ticker'], start_dt, end_dt)
                if not prices.empty:
                    target_raw_data[target['key']] = (prices, divs)
                    for d in prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week]).tail(1).index:
                        global_dates.add(d.strftime('%Y/%m/%d'))
            
            chart_labels = sorted(list(global_dates))

            for target in targets:
                t_key = target['key']
                if t_key not in target_raw_data: continue
                
                prices, divs = target_raw_data[t_key]
                strat = target['strategy']
                
                # 매수일 계산
                if strat == "거치식 (일괄 매수)": invest_dates = [prices.index[0]]
                elif strat == "적립식 (매일)": invest_dates = prices.index
                elif strat == "적립식 (매주)": invest_dates = prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week]).head(1).index
                else: invest_dates = prices.groupby([prices.index.year, prices.index.month]).head(1).index

                installment = INITIAL_CASH / len(invest_dates)
                invest_dates_set = set(invest_dates)
                div_dates_set = set(divs.index)
                
                reserve_cash, available_cash, total_shares = INITIAL_CASH, 0.0, 0
                history, summary, asset_by_date = [], [], {}
                prev_asset = INITIAL_CASH

                # 일 단위 루프 (배당금 체크를 위해)
                for date, price in prices.items():
                    # 1. 배당금 수취
                    if date in div_dates_set and total_shares > 0:
                        div_amount = total_shares * float(divs[date])
                        available_cash += div_amount
                        history.append({
                            '날짜': date.strftime('%Y/%m/%d'), '구분': '배당금', '단가': float(divs[date]),
                            '수량': int(total_shares), '거래금액': div_amount, '현금잔고': float(reserve_cash + available_cash),
                            '총자산': float(reserve_cash + available_cash + (total_shares * price))
                        })

                    # 2. 매수 (배당금 포함 전액 재투자 로직)
                    if date in invest_dates_set:
                        reserve_cash -= installment
                        available_cash += installment
                        
                        shares_to_buy = int(available_cash // float(price))
                        if shares_to_buy > 0:
                            available_cash -= shares_to_buy * float(price)
                            total_shares += shares_to_buy
                            history.append({
                                '날짜': date.strftime('%Y/%m/%d'), '구분': '매수', '단가': float(price),
                                '수량': shares_to_buy, '거래금액': float(shares_to_buy * price),
                                '현금잔고': float(reserve_cash + available_cash),
                                '총자산': float(reserve_cash + available_cash + (total_shares * price))
                            })
                    
                    # 주간 평가 데이터 저장
                    label = date.strftime('%Y/%m/%d')
                    if label in chart_labels:
                        cur_asset = float(reserve_cash + available_cash + (total_shares * price))
                        asset_by_date[label] = cur_asset
                        summary.append({
                            '기간': label, '기말단가': float(price), '기말자산': cur_asset,
                            '증감': float(cur_asset - prev_asset), '수익률': float(((cur_asset / INITIAL_CASH) - 1) * 100)
                        })
                        prev_asset = cur_asset

                chart_vals = []
                last_val = INITIAL_CASH
                for lbl in chart_labels:
                    if lbl in asset_by_date: last_val = asset_by_date[lbl]
                    chart_vals.append(last_val)

                all_sim_data[t_key] = {
                    'name': target['name'], 'summary': summary, 'history': history,
                    'chart_values': chart_vals, 'final_asset': prev_asset,
                    'total_profit': prev_asset - INITIAL_CASH, 'profit_rate': ((prev_asset / INITIAL_CASH) - 1) * 100
                }

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH, 'compare_keys': [k for k in compare_keys if k in all_sim_data],
                'labels': chart_labels, 'all_data': all_sim_data
            }
            st.session_state.run_clicked, st.session_state.show_settings = True, False
            st.rerun()

# ==========================================
# 결과 출력 영역 (기존과 동일한 UI 구조 유지)
# ==========================================
if st.session_state.run_clicked and st.session_state.sim_result_data:
    res = st.session_state.sim_result_data
    datasets = []
    colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b']
    for idx, k in enumerate(res['compare_keys']):
        d = res['all_data'][k]
        datasets.append({'label': d['name'], 'data': d['chart_values'], 'borderColor': colors[idx % 4], 'tension': 0.3, 'fill': False})

    html_code = f"""
    <!DOCTYPE html><html><head><meta charset="utf-8"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; }}
        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .card {{ background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-top: 4px solid #94a3b8; }}
        .card h3 {{ font-size: 13px; margin: 0 0 10px 0; }}
        .card-row {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; }}
        .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 350px; margin-bottom: 20px; }}
        .table-wrapper {{ overflow-x: auto; background: white; border-radius: 10px; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
        th {{ background: #f1f5f9; padding: 10px; border-bottom: 1px solid #e2e8f0; }}
        td {{ padding: 8px; border-bottom: 1px solid #f1f5f9; text-align: center; }}
        .badge {{ padding: 2px 5px; border-radius: 4px; color: white; font-size: 10px; }}
        .buy {{ background: #ef4444; }} .div {{ background: #10b981; }}
    </style>
    </head><body>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    <div class="card-grid" id="stat-cards"></div>
    <div style="font-weight:700; margin: 20px 0 10px 0;">🔍 상세 거래 내역 (배당 포함)</div>
    <select id="ticker-select" style="padding: 5px; margin-bottom: 10px;" onchange="renderTable()"></select>
    <div class="table-wrapper"><table><thead><tr><th>날짜</th><th>구분</th><th>단가/분배금</th><th>수량</th><th>금액</th><th>현금잔고</th><th>총자산</th></tr></thead><tbody id="tbody"></tbody></table></div>
    <script>
        const data = {json.dumps(res['all_data'])};
        const keys = {json.dumps(res['compare_keys'])};
        const labels = {json.dumps(res['labels'])};
        
        const sel = document.getElementById('ticker-select');
        keys.forEach(k => sel.add(new Option(data[k].name, k)));

        function fmt(v) {{ return Math.floor(v).toLocaleString() + "원"; }}

        function renderTable() {{
            const k = sel.value;
            const d = data[k];
            document.getElementById('stat-cards').innerHTML = keys.map(key => {{
                const item = data[key];
                return `<div class="card" style="border-top-color: ${{key===k?'#ef4444':'#94a3b8'}}">
                    <h3>${{item.name}}</h3>
                    <div class="card-row"><span>최종 자산</span><strong>${{fmt(item.final_asset)}}</strong></div>
                    <div class="card-row"><span>수익률</span><span style="color:red">${{item.profit_rate.toFixed(2)}}%</span></div>
                </div>`;
            }}).join('');
            
            document.getElementById('tbody').innerHTML = d.history.slice().reverse().map(h => `
                <tr><td>${{h.날짜}}</td><td><span class="badge ${{h.구분==='배당금'?'div':'buy'}}">${{h.구분}}</span></td>
                <td>${{fmt(h.단가)}}</td><td>${{h.수량}}</td><td>${{fmt(h.거래금액)}}</td><td>${{fmt(h.현금잔고)}}</td><td><strong>${{fmt(h.총자산)}}</strong></td></tr>
            `).join('');
        }}

        new Chart(document.getElementById('assetChart'), {{
            type: 'line', data: {{ labels: labels, datasets: {json.dumps(datasets)} }},
            options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ ticks: {{ callback: v => (v/10000) + '만' }} }} }} }}
        }});
        renderTable();
    </script></body></html>
    """
    components.html(html_code, height=1500, scrolling=True)

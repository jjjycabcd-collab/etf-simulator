import sys
import pandas as pd
import re
import json
import datetime
import requests
import io
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정 및 상태 초기화
# ==========================================
st.set_page_config(page_title="월배당 ETF 백테스트", layout="wide")

# 앱 상태 관리
if 'show_settings' not in st.session_state:
    st.session_state.show_settings = True
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False
if 'sim_result_data' not in st.session_state:
    st.session_state.sim_result_data = None

# --- 입력값 유지를 위한 세션 상태 초기화 ---
if 'saved_cash' not in st.session_state:
    st.session_state.saved_cash = "5,000,000"
if 'saved_period' not in st.session_state:
    st.session_state.saved_period = "2025.1~2026.4"
if 'saved_div_action' not in st.session_state:
    st.session_state.saved_div_action = "재투자"
if 'saved_etf' not in st.session_state:
    st.session_state.saved_etf = "498400, 472150, 498400 + 472150"
if 'saved_strategy' not in st.session_state:
    st.session_state.saved_strategy = ["거치식 (일괄 매수)"]

# ==========================================
# 종목 데이터 마스터 적재 (캐싱)
# ==========================================
@st.cache_data(ttl=86400)
def load_all_tickers():
    """국내 상장 주식 및 ETF 전체 목록 수집"""
    tickers = {}
    try:
        url = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        df_list = pd.read_html(io.StringIO(res.text), header=0)
        if df_list:
            df = df_list[0]
            for _, row in df.iterrows():
                code = str(row['종목코드']).zfill(6)
                tickers[code] = row['회사명']
    except Exception:
        fallback = {"005930": "삼성전자", "000660": "SK하이닉스"}
        tickers.update(fallback)

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json().get('result', {}).get('etfItemList', []):
                tickers[item['itemcode']] = item['itemname']
    except Exception:
        pass
        
    return tickers

ALL_TICKERS = load_all_tickers()

# ==========================================
# 함수 정의부
# ==========================================
@st.cache_data(ttl=86400)
def get_stock_info(code):
    """종목명 가져오기"""
    if not code: return ""
    if code in ALL_TICKERS:
        return f"{ALL_TICKERS[code]}({code})"
    try:
        check_code = f"{code}.KS" if code.isdigit() else code
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
        df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty and code.isdigit():
            ticker_code = f"{code}.KQ"
            ticker = yf.Ticker(ticker_code)
            df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty: 
            return pd.Series(dtype=float), pd.Series(dtype=float)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close'].dropna(), df['Dividends'].replace(0, pd.NA).dropna()
    except:
        return pd.Series(dtype=float), pd.Series(dtype=float)

# ==========================================
# UI 영역
# ==========================================
if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 테스트 환경 다시 설정하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

if st.session_state.show_settings:
    st.title("월 배당 ETF 백테스트")

    st.info("""
    💡 **참고사항 (데이터 한계 및 기준)**
    * **순수 종가 사용:** 수정주가가 아닌 실제 거래된 일별 종가를 기준으로 계산합니다.
    * **배당 기준 시점:** '배당락일' 당일 종가에 전액 재투자되는 것으로 가정합니다.
    * **배당풍차 모드 (A + B):** `498400 + 472150`과 같이 입력 시 배당락일에 종목을 교체합니다.
    """)

    with st.expander("🔍 종목 코드를 모르시나요? (이름으로 코드 검색하기)", expanded=False):
        search_kw = st.text_input("찾고 싶은 국내 주식이나 ETF 이름을 입력하세요.", key="search_input")
        if search_kw:
            search_kw_clean = search_kw.replace(" ", "").lower()
            matches = []
            for code, name in ALL_TICKERS.items():
                if search_kw_clean in name.replace(" ", "").lower() or search_kw_clean == code.lower():
                    matches.append((code, name))
            if matches:
                for code, name in matches[:10]:
                    st.markdown(f"- **{name}** : `{code}`")

    with st.container(border=True):
        st.subheader("⚙️ 테스트 환경")
        with st.form("settings_form"):
            col1, col2 = st.columns(2)
            with col1:
                cash_input = st.text_input("초기 총 투자금 (원)", value=st.session_state.saved_cash)
                period_input = st.text_input("백테스트 기간", value=st.session_state.saved_period)
                div_idx = 0 if st.session_state.saved_div_action == "재투자" else 1
                div_action_input = st.radio("배당금 처리", ["재투자", "인출(생활비)"], index=div_idx, horizontal=True)
            with col2:
                etf_input = st.text_input("종목 코드 (최대 4개)", value=st.session_state.saved_etf)
                strategy_options = st.multiselect(
                    "분할 매수 방식",
                    ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                    default=st.session_state.saved_strategy
                )
            run_btn = st.form_submit_button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        # 입력값 세션에 저장
        st.session_state.saved_cash = cash_input
        st.session_state.saved_period = period_input
        st.session_state.saved_div_action = div_action_input
        st.session_state.saved_etf = etf_input
        st.session_state.saved_strategy = strategy_options

        with st.spinner('데이터 분석 중...'):
            INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            try:
                if '~' in period_input:
                    s_str, e_str = period_input.split('~')
                    start_dt = pd.to_datetime(s_str.strip().replace('.', '-')) if '.' in s_str else pd.to_datetime(f"{s_str.strip()}-01-01")
                    end_dt = pd.to_datetime(e_str.strip().replace('.', '-')) + pd.offsets.MonthEnd(0) if '.' in e_str else pd.to_datetime(f"{e_str.strip()}-12-31")
                else:
                    start_dt = pd.to_datetime(f"{period_input.strip()}-01-01")
                    end_dt = pd.to_datetime(f"{period_input.strip()}-12-31")
            except:
                start_dt, end_dt = pd.to_datetime("2025-01-01"), pd.to_datetime("2026-04-30")

            raw_target_strs = [t.strip().upper() for t in etf_input.split(',') if t.strip()][:4]
            targets = []
            compare_keys = []
            
            if len(raw_target_strs) == 1 and '+' not in raw_target_strs[0]:
                strats = strategy_options if strategy_options else ["거치식 (일괄 매수)"]
                for strat in strats:
                    key = f"{raw_target_strs[0]}_{strat}"
                    targets.append({'key': key, 'ticker': raw_target_strs[0], 'strategy': strat, 'name': f"{get_stock_info(raw_target_strs[0])} ({strat})"})
                    compare_keys.append(key)
            else:
                for t in raw_target_strs:
                    name = f"배당풍차 ({t})" if '+' in t else f"{get_stock_info(t)}"
                    targets.append({'key': t, 'ticker': t, 'strategy': "거치식 (일괄 매수)", 'name': name})
                    compare_keys.append(t)

            all_tickers_needed = set()
            for target in targets:
                for tk in target['ticker'].split('+'):
                    all_tickers_needed.add(tk.strip())

            target_raw_data = {}
            for tk in all_tickers_needed:
                p, d = fetch_prices_and_dividends(tk, start_dt, end_dt)
                if not p.empty:
                    target_raw_data[tk] = (p, d)

            if not target_raw_data:
                st.error("종목 데이터를 불러올 수 없습니다.")
                st.stop()

            all_trading_dates = sorted(list(set(d for p, _ in target_raw_data.values() for d in p.index)))
            processed_data = {tk: (p.reindex(all_trading_dates).ffill(), d.reindex(all_trading_dates).fillna(0.0)) for tk, (p, d) in target_raw_data.items()}

            temp_s = pd.Series(index=all_trading_dates, data=range(len(all_trading_dates)))
            eow_dates_set = set(temp_s.groupby([temp_s.index.isocalendar().year, temp_s.index.isocalendar().week]).tail(1).index)
            eom_dates_set = set(temp_s.groupby([temp_s.index.year, temp_s.index.month]).tail(1).index)
            chart_labels = sorted([d.strftime('%Y/%m/%d') for d in eow_dates_set])

            all_sim_data = {}
            for target in targets:
                t_key = target['key']
                t_tickers = [tk.strip() for tk in target['ticker'].split('+') if tk.strip()]
                if any(tk not in processed_data for tk in t_tickers): continue
                
                strat = target['strategy']
                if strat == "거치식 (일괄 매수)": invest_dates_set = {all_trading_dates[0]}
                elif strat == "적립식 (매일)": invest_dates_set = set(all_trading_dates)
                elif strat == "적립식 (매주)": invest_dates_set = set(temp_s.groupby([temp_s.index.isocalendar().year, temp_s.index.isocalendar().week]).head(1).index)
                else: invest_dates_set = set(temp_s.groupby([temp_s.index.year, temp_s.index.month]).head(1).index)

                installment = INITIAL_CASH / len(invest_dates_set) if len(invest_dates_set) > 0 else 0
                reserve_cash, available_cash, total_shares = INITIAL_CASH, 0.0, 0
                total_withdrawn, total_dividend = 0.0, 0.0 
                history, summary, asset_by_date, monthly_data = [], [], {}, {}
                prev_asset = INITIAL_CASH
                reinvest_flag, windmill_swap_flag = False, False
                current_idx = 0
                current_ticker = t_tickers[current_idx]

                for date in all_trading_dates:
                    price = processed_data[current_ticker][0][date]
                    if pd.isna(price): continue 

                    month_str = date.strftime('%Y.%m')
                    if month_str not in monthly_data:
                        monthly_data[month_str] = {'div_per_share': 0.0, 'div_total': 0.0, 'end_asset': 0.0, 'end_price': 0.0}

                    div = processed_data[current_ticker][1][date]
                    if div > 0 and total_shares > 0:
                        div_amount = total_shares * float(div)
                        monthly_data[month_str]['div_per_share'] += float(div)
                        monthly_data[month_str]['div_total'] += div_amount
                        total_dividend += div_amount 
                        if div_action_input == "재투자": available_cash += div_amount
                        else: total_withdrawn += div_amount
                        history.append({'날짜': date.strftime('%Y/%m/%d'), '구분': '배당금', '종목': current_ticker, '단가': float(div), '수량': int(total_shares), '거래금액': div_amount, '현금잔고': float(reserve_cash + available_cash), '총자산': float(reserve_cash + available_cash + (total_shares * price))})
                        if len(t_tickers) > 1: windmill_swap_flag = True
                        elif div_action_input == "재투자": reinvest_flag = True

                    is_invest_day = date in invest_dates_set
                    if is_invest_day:
                        reserve_cash -= installment
                        available_cash += installment

                    if windmill_swap_flag:
                        sell_amount = total_shares * price
                        available_cash += sell_amount
                        total_shares = 0
                        current_idx = (current_idx + 1) % len(t_tickers)
                        current_ticker = t_tickers[current_idx]
                        price = processed_data[current_ticker][0][date]
                        windmill_swap_flag, reinvest_flag = False, True 

                    if is_invest_day or reinvest_flag:
                        if not pd.isna(price):
                            shares_to_buy = int(available_cash // price)
                            if shares_to_buy > 0:
                                available_cash -= shares_to_buy * price
                                total_shares += shares_to_buy
                        reinvest_flag = False
                    
                    cur_asset = float(reserve_cash + available_cash + (total_shares * price))
                    monthly_data[month_str]['end_asset'] = cur_asset
                    monthly_data[month_str]['end_price'] = float(price)
                    label = date.strftime('%Y/%m/%d')
                    if label in chart_labels:
                        asset_by_date[label] = cur_asset
                        summary.append({'기간': label, '기말단가': float(price), '기말자산': cur_asset, '수익률': float(((cur_asset / INITIAL_CASH) - 1) * 100)})
                        prev_asset = cur_asset

                chart_vals = [asset_by_date.get(lbl, INITIAL_CASH) for lbl in chart_labels]
                final_eval_asset = float(reserve_cash + available_cash + (total_shares * float(processed_data[current_ticker][0][all_trading_dates[-1]])))
                
                monthly_list, prev_m_asset = [], INITIAL_CASH
                for m_str in sorted(monthly_data.keys()):
                    m_data = monthly_data[m_str]
                    div_yield = (m_data['div_per_share'] / m_data['end_price'] * 100) if m_data['end_price'] > 0 else 0.0
                    monthly_list.append({'기간': m_str, '주당배당': m_data['div_per_share'], '배당률': div_yield, '배당합계': m_data['div_total'], '기말자산': m_data['end_asset'], '증감': m_data['end_asset'] - prev_m_asset})
                    prev_m_asset = m_data['end_asset']
                
                real_total_asset = final_eval_asset + total_withdrawn
                all_sim_data[t_key] = {'name': target['name'], 'summary': summary, 'history': history, 'monthly_summary': monthly_list, 'chart_values': chart_vals, 'final_asset': final_eval_asset, 'div_action': div_action_input, 'initial_cash': INITIAL_CASH, 'total_dividend': total_dividend, 'total_withdrawn': total_withdrawn, 'total_profit': real_total_asset - INITIAL_CASH, 'profit_rate': ((real_total_asset / INITIAL_CASH) - 1) * 100}

            st.session_state.sim_result_data = {'initial_cash': INITIAL_CASH, 'compare_keys': [k for k in compare_keys if k in all_sim_data], 'labels': chart_labels, 'all_data': all_sim_data}
            st.session_state.run_clicked, st.session_state.show_settings = True, False
            st.rerun()

# ==========================================
# 결과 출력 영역
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
        .card h3 {{ font-size: 14px; margin: 0 0 10px 0; }}
        .card-row {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 5px; }}
        .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 350px; margin-bottom: 20px; }}
        .table-wrapper {{ overflow-x: auto; background: white; border-radius: 10px; margin-bottom: 30px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #f8fafc; padding: 12px; border-bottom: 1px solid #e2e8f0; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; }}
    </style>
    </head><body>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    <div class="card-grid" id="stat-cards"></div>
    <div class="table-wrapper"><table><thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead><tbody id="monthly-tbody"></tbody></table></div>
    <script>
        const data = {json.dumps(res['all_data'])};
        const keys = {json.dumps(res['compare_keys'])};
        const labels = {json.dumps(res['labels'])};
        function fmt(v) {{ return Math.floor(v).toLocaleString() + "원"; }}
        function renderTable() {{
            const k = keys[0]; const d = data[k];
            document.getElementById('stat-cards').innerHTML = keys.map(key => {{
                const item = data[key];
                return `<div class="card"><h3>${{item.name}}</h3><div class="card-row"><span>초기 투자금</span><strong>${{fmt(item.initial_cash)}}</strong></div><div class="card-row"><span>총 배당금</span><span style="color:#d97706;">+${{fmt(item.total_dividend)}}</span></div><div class="card-row"><span>최종 자산</span><strong>${{fmt(item.final_asset)}}</strong></div><div class="card-row"><span>총 수익률</span><span style="color:${{item.total_profit>=0?'#dc2626':'#2563eb'}};">${{item.profit_rate.toFixed(2)}}%</span></div></div>`;
            }}).join('');
            document.getElementById('monthly-tbody').innerHTML = d.monthly_summary.reverse().map(m => `<tr><td>${{m.기간}}</td><td>${{Math.floor(m.주당배당).toLocaleString()}}</td><td>${{m.배당률.toFixed(2)}}%</td><td>${{fmt(m.배당합계)}}</td><td>${{fmt(m.기말자산)}}</td><td style="color:${{m.증감>0?'#dc2626':'#2563eb'}};">${{fmt(m.증감)}}</td></tr>`).join('');
        }}
        new Chart(document.getElementById('assetChart'), {{ type: 'line', data: {{ labels: labels, datasets: {json.dumps(datasets)} }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
        renderTable();
    </script></body></html>
    """
    components.html(html_code, height=1200, scrolling=True)

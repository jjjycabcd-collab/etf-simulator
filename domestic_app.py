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
st.set_page_config(page_title="월배당 ETF 백테스트", layout="wide")

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
    """가격(순수 종가) 및 배당 데이터 수집"""
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
st.title("🇰🇷 월배당 ETF 백테스트")

# 💡 상단 네비게이션 (국내/해외 링크 분기)
st.markdown("<br>", unsafe_allow_html=True)
col_nav1, col_nav2 = st.columns(2)
with col_nav1:
    # 현재 페이지 (버튼 형태로 시각적 강조)
    st.button("🇰🇷 국내 월배당 ETF (현재 페이지)", type="primary", use_container_width=True, disabled=True)
with col_nav2:
    # 해외 페이지 링크
    st.link_button("🌎 해외 ETF 시뮬레이터 이동", "https://etf-simulator-qqq.streamlit.app/", use_container_width=True)
st.markdown("<br>", unsafe_allow_html=True)

st.info("""
💡 **참고사항 (데이터 한계 및 기준)**

* **순수 종가 사용:** 본 시뮬레이터는 배당 수익 이중 계산 방지를 위해 수정주가(Adj Close)가 아닌 **실제 거래된 일별 종가(Close)**를 기준으로 단가를 계산합니다.
* **배당 기준 시점:** yfinance에서 제공하는 배당 기준일은 실제 입금일이 아닌 **'배당락일(Ex-Dividend Date)'**입니다. 재투자 모드 시 배당락일 당일 종가에 전액 재투자되는 것으로 백테스트가 진행됩니다.
* **배당풍차 모드 (A + B):** 입력창에 `498400 + 472150`과 같이 `+`로 연결하여 입력하면 **배당풍차 모드**가 작동합니다. A종목 보유 중 배당락일(배당 수취 확정)이 도래하면, 당일 종가에 A종목을 전량 매도하고 즉시 B종목으로 교차 매수하여 배당 주기를 극대화합니다.
""")

if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 테스트 환경 다시 설정하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 테스트 환경")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 총 투자금 (원)", "40,000,000")
            period_input = st.text_input("백테스트 기간", "2025~2026")
            div_action_input = st.radio("배당금 처리", ["재투자", "인출(생활비)"], horizontal=True)

        with col2:
            etf_input = st.text_input("종목 코드 (최대 4개)", "498400, 472150, 498400 + 472150")
            strategy_options = st.multiselect(
                "분할 매수 방식 (단일 종목 시 적용)",
                ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                default=["거치식 (일괄 매수)"]
            )
            
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('배당풍차 및 주가 데이터를 통합 분석 중...'):
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

            # 타겟 파싱
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

            # 전체 필요 종목(티커) 단위로 데이터 수집
            all_tickers_needed = set()
            for target in targets:
                for tk in target['ticker'].split('+'):
                    all_tickers_needed.add(tk.strip())

            target_raw_data = {}
            for tk in all_tickers_needed:
                p, d = fetch_prices_and_dividends(tk, start_dt, end_dt)
                if not p.empty:
                    target_raw_data[tk] = (p, d)

            # 모든 거래일을 포괄하는 통합 날짜 리스트 생성
            all_trading_dates = sorted(list(set(d for p, _ in target_raw_data.values() for d in p.index)))

            # 결측치 방지를 위한 데이터 Forward Fill
            processed_data = {}
            for tk, (p, d) in target_raw_data.items():
                processed_data[tk] = (p.reindex(all_trading_dates).ffill(), d.reindex(all_trading_dates).fillna(0.0))

            temp_s = pd.Series(index=all_trading_dates, data=range(len(all_trading_dates)))
            eow_dates_set = set(temp_s.groupby([temp_s.index.isocalendar().year, temp_s.index.isocalendar().week]).tail(1).index)
            eom_dates_set = set(temp_s.groupby([temp_s.index.year, temp_s.index.month]).tail(1).index)
            chart_labels = sorted([d.strftime('%Y/%m/%d') for d in eow_dates_set])

            all_sim_data = {}

            for target in targets:
                t_key = target['key']
                t_tickers = [tk.strip() for tk in target['ticker'].split('+') if tk.strip()]
                is_windmill = len(t_tickers) > 1
                
                if any(tk not in processed_data for tk in t_tickers): continue
                
                strat = target['strategy']
                if strat == "거치식 (일괄 매수)": invest_dates_set = {all_trading_dates[0]}
                elif strat == "적립식 (매일)": invest_dates_set = set(all_trading_dates)
                elif strat == "적립식 (매주)": invest_dates_set = set(temp_s.groupby([temp_s.index.isocalendar().year, temp_s.index.isocalendar().week]).head(1).index)
                else: invest_dates_set = set(temp_s.groupby([temp_s.index.year, temp_s.index.month]).head(1).index)

                installment = INITIAL_CASH / len(invest_dates_set) if len(invest_dates_set) > 0 else 0
                
                reserve_cash, available_cash, total_shares = INITIAL_CASH, 0.0, 0
                total_withdrawn, total_dividend = 0.0, 0.0 
                history, summary, asset_by_date = [], [], {}
                monthly_data = {}
                prev_asset = INITIAL_CASH
                
                reinvest_flag = False
                windmill_swap_flag = False
                
                current_idx = 0
                current_ticker = t_tickers[current_idx]

                for date in all_trading_dates:
                    price = processed_data[current_ticker][0][date]
                    if pd.isna(price): continue # 아직 상장 전

                    month_str = date.strftime('%Y.%m')
                    if month_str not in monthly_data:
                        monthly_data[month_str] = {'div_per_share': 0.0, 'div_total': 0.0, 'end_asset': 0.0, 'end_price': 0.0}

                    # 1. 배당금 확인 (어제까지 보유한 종목 기준)
                    div = processed_data[current_ticker][1][date]
                    if div > 0 and total_shares > 0:
                        div_amount = total_shares * float(div)
                        monthly_data[month_str]['div_per_share'] += float(div)
                        monthly_data[month_str]['div_total'] += div_amount
                        total_dividend += div_amount 
                        
                        action_gubun = '배당금' if div_action_input == "재투자" else '배당금(인출)'
                        if div_action_input == "재투자":
                            available_cash += div_amount
                        else:
                            total_withdrawn += div_amount

                        history.append({
                            '날짜': date.strftime('%Y/%m/%d'), '구분': action_gubun, '종목': current_ticker,
                            '단가': float(div), '수량': int(total_shares), '거래금액': div_amount,
                            '현금잔고': float(reserve_cash + available_cash),
                            '총자산': float(reserve_cash + available_cash + (total_shares * price))
                        })

                        # 풍차모드인 경우 배당금 액션 직후 무조건 교체 매매 플래그 ON
                        if is_windmill:
                            windmill_swap_flag = True
                        elif div_action_input == "재투자":
                            reinvest_flag = True

                    # 2. 투자금 투입 (정기 매수일)
                    is_invest_day = date in invest_dates_set
                    if is_invest_day:
                        reserve_cash -= installment
                        available_cash += installment

                    # 3. 풍차 매도 (교체 스왑)
                    if windmill_swap_flag:
                        sell_amount = total_shares * price
                        available_cash += sell_amount
                        history.append({
                            '날짜': date.strftime('%Y/%m/%d'), '구분': '풍차매도', '종목': current_ticker,
                            '단가': float(price), '수량': int(total_shares), '거래금액': sell_amount,
                            '현금잔고': float(reserve_cash + available_cash),
                            '총자산': float(reserve_cash + available_cash)
                        })
                        total_shares = 0
                        
                        # 타겟 종목 스위칭
                        current_idx = (current_idx + 1) % len(t_tickers)
                        current_ticker = t_tickers[current_idx]
                        price = processed_data[current_ticker][0][date] # 교체된 종목의 오늘 종가
                        
                        windmill_swap_flag = False
                        reinvest_flag = True # 새 종목 매수를 위해 재투자 플래그 ON

                    # 4. 매수 실행 (정기 매수 OR 풍차/배당 재투자)
                    if is_invest_day or reinvest_flag:
                        if not pd.isna(price):
                            shares_to_buy = int(available_cash // price)
                            if shares_to_buy > 0:
                                available_cash -= shares_to_buy * price
                                total_shares += shares_to_buy
                                
                                gubun_text = '매수'
                                if reinvest_flag and not is_invest_day:
                                    gubun_text = '풍차매수' if is_windmill else '배당재투자'
                                elif reinvest_flag and is_invest_day:
                                    gubun_text = '매수+풍차' if is_windmill else '매수+재투자'

                                history.append({
                                    '날짜': date.strftime('%Y/%m/%d'), '구분': gubun_text, '종목': current_ticker,
                                    '단가': float(price), '수량': shares_to_buy, '거래금액': float(shares_to_buy * price),
                                    '현금잔고': float(reserve_cash + available_cash),
                                    '총자산': float(reserve_cash + available_cash + (total_shares * price))
                                })
                        reinvest_flag = False
                    
                    # 5. 자산 평가 및 월말평가 기록
                    cur_asset = float(reserve_cash + available_cash + (total_shares * price))
                    
                    if date in eom_dates_set and date != all_trading_dates[-1]:
                        history.append({
                            '날짜': date.strftime('%Y/%m/%d'), '구분': '월말평가', '종목': current_ticker,
                            '단가': float(price), '수량': int(total_shares), '거래금액': 0.0, 
                            '현금잔고': float(reserve_cash + available_cash), '총자산': cur_asset
                        })
                    
                    monthly_data[month_str]['end_asset'] = cur_asset
                    monthly_data[month_str]['end_price'] = float(price)
                    
                    label = date.strftime('%Y/%m/%d')
                    if label in chart_labels:
                        asset_by_date[label] = cur_asset
                        summary.append({
                            '기간': label, '기말단가': float(price), '기말자산': cur_asset,
                            '증감': float(cur_asset - prev_asset), '수익률': float(((cur_asset / INITIAL_CASH) - 1) * 100)
                        })
                        prev_asset = cur_asset

                # 차트 및 최종 평가 처리
                chart_vals = []
                last_val = INITIAL_CASH
                for lbl in chart_labels:
                    if lbl in asset_by_date: last_val = asset_by_date[lbl]
                    chart_vals.append(last_val)

                last_date = all_trading_dates[-1]
                last_price = float(processed_data[current_ticker][0][last_date])
                final_eval_asset = float(reserve_cash + available_cash + (total_shares * last_price))
                
                history.append({
                    '날짜': last_date.strftime('%Y/%m/%d'), '구분': '최종평가', '종목': current_ticker,
                    '단가': last_price, '수량': int(total_shares), '거래금액': 0.0, 
                    '현금잔고': float(reserve_cash + available_cash), '총자산': final_eval_asset
                })

                monthly_list = []
                prev_m_asset = INITIAL_CASH
                for m_str in sorted(monthly_data.keys()):
                    m_data = monthly_data[m_str]
                    div_yield = (m_data['div_per_share'] / m_data['end_price'] * 100) if m_data['end_price'] > 0 else 0.0
                    change = m_data['end_asset'] - prev_m_asset
                    
                    monthly_list.append({
                        '기간': m_str, '주당배당': m_data['div_per_share'], '배당률': div_yield,
                        '배당합계': m_data['div_total'], '기말자산': m_data['end_asset'], '증감': change
                    })
                    prev_m_asset = m_data['end_asset']
                    
                real_total_asset = final_eval_asset + total_withdrawn

                all_sim_data[t_key] = {
                    'name': target['name'], 'summary': summary, 'history': history,
                    'monthly_summary': monthly_list,
                    'chart_values': chart_vals, 'final_asset': final_eval_asset,
                    'div_action': div_action_input, 'initial_cash': INITIAL_CASH, 
                    'total_dividend': total_dividend, 'total_withdrawn': total_withdrawn,
                    'total_profit': real_total_asset - INITIAL_CASH, 
                    'profit_rate': ((real_total_asset / INITIAL_CASH) - 1) * 100
                }

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH, 'compare_keys': [k for k in compare_keys if k in all_sim_data],
                'labels': chart_labels, 'all_data': all_sim_data
            }
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
        body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; }}
        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .card {{ background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-top: 4px solid #94a3b8; }}
        .card h3 {{ font-size: 14px; margin: 0 0 10px 0; color:#1e293b; font-weight:700; }}
        .card-row {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 5px; color:#475569; }}
        .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 350px; margin-bottom: 20px; }}
        .table-wrapper {{ overflow-x: auto; background: white; border-radius: 10px; margin-bottom: 30px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #f8fafc; padding: 12px 10px; border-bottom: 1px solid #e2e8f0; color: #475569; font-weight: 600; border-top: 1px solid #e2e8f0; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; }}
        
        /* 짝수 행 음영 처리 및 Hover */
        tbody tr:nth-child(even) {{ background-color: #f8fafc; }}
        tbody tr:hover {{ background-color: #f1f5f9; transition: background-color 0.2s ease; }}
        
        .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; min-width: 45px; text-align: center;}}
        .buy {{ background: #ef4444; }} 
        .sell {{ background: #3b82f6; }} /* 풍차매도 파란색 뱃지 */
        .div {{ background: #10b981; }}
        .withdraw {{ background: #f59e0b; }} 
        .reinvest {{ background: #8b5cf6; }} /* 풍차매수, 배당재투자 보라색 */
        .eval {{ background: #64748b; }}
        .eval-month {{ background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1; }}
        .header-flex {{ display: flex; justify-content: space-between; align-items: center; margin: 25px 0 10px 0; }}
        .sort-select {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #cbd5e1; font-size: 13px; background: white; font-weight: 600; color: #475569; outline: none; cursor: pointer; }}
        .section-icon {{ border-left: 3px solid #3b82f6; padding-left: 8px; }}
    </style>
    </head><body>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    <div class="card-grid" id="stat-cards"></div>
    
    <div style="margin-bottom: 15px; display:flex; justify-content:flex-end;">
        <select id="ticker-select" class="sort-select" style="min-width: 250px;" onchange="renderTable()"></select>
    </div>

    <div class="header-flex">
        <div style="display:flex; align-items:center; gap:10px;">
            <span class="section-icon" style="font-weight:700; font-size:16px;">🗓️ 월별 요약</span>
        </div>
        <select id="sort-select-monthly" class="sort-select" onchange="renderTable()">
            <option value="desc">최신순</option>
            <option value="asc">과거순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead>
            <tbody id="monthly-tbody"></tbody>
        </table>
    </div>
    
    <div class="header-flex">
        <div style="display:flex; align-items:center; gap:10px;">
            <span class="section-icon" style="font-weight:700; font-size:16px;">🔍 상세 거래 내역</span>
        </div>
        <select id="sort-select-history" class="sort-select" onchange="renderTable()">
            <option value="desc">최신순</option>
            <option value="asc">과거순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table>
            <thead>
                <tr><th>날짜</th><th>구분</th><th>종목</th><th>단가/분배금</th><th>수량</th><th>금액</th><th>현금잔고</th><th>총자산</th></tr>
            </thead>
            <tbody id="tbody"></tbody>
        </table>
    </div>
    
    <script>
        const data = {json.dumps(res['all_data'])};
        const keys = {json.dumps(res['compare_keys'])};
        const labels = {json.dumps(res['labels'])};
        
        const sel = document.getElementById('ticker-select');
        keys.forEach(k => sel.add(new Option(data[k].name, k)));

        function fmt(v) {{ return Math.floor(v).toLocaleString() + "원"; }}
        function fmtMan(v) {{
            if (v === 0) return "0";
            const isNeg = v < 0; let absV = Math.abs(v);
            if (absV < 10000) return (isNeg ? "-" : "") + Math.floor(absV).toLocaleString() + "원";
            return (isNeg ? "-" : "") + Math.floor(absV / 10000).toLocaleString() + "만";
        }}
        function colorForChange(v) {{ return v > 0 ? '#dc2626' : (v < 0 ? '#2563eb' : '#334155'); }}

        function getBadgeClass(type) {{
            if(type.includes('풍차매도')) return 'sell';
            if(type.includes('풍차매수') || type.includes('재투자')) return 'reinvest';
            if(type.includes('월말평가')) return 'eval-month';
            if(type.includes('배당금(인출)')) return 'withdraw';
            if(type.includes('배당금')) return 'div';
            if(type.includes('최종평가')) return 'eval';
            return 'buy';
        }}

        function renderTable() {{
            const k = sel.value;
            const d = data[k];
            
            // 요약 카드 렌더링
            document.getElementById('stat-cards').innerHTML = keys.map(key => {{
                const item = data[key];
                const isWithdrawal = item.div_action === '인출(생활비)';
                const assetLabel = isWithdrawal ? '평가 자산' : '최종 자산';
                
                let withdrawRow = '';
                if (isWithdrawal) {{
                    withdrawRow = `<div class="card-row"><span>누적 인출금</span><span style="color:#10b981; font-weight:600;">+${{fmt(item.total_withdrawn)}}</span></div>`;
                }}
                
                return `<div class="card" style="border-top-color: ${{key===k?'#ef4444':'#94a3b8'}}">
                    <h3>${{item.name}}</h3>
                    <div class="card-row"><span>초기 투자금</span><strong>${{fmt(item.initial_cash)}}</strong></div>
                    <div class="card-row"><span>총 배당금</span><span style="color:#d97706; font-weight:600;">+${{fmt(item.total_dividend)}}</span></div>
                    <div class="card-row"><span>${{assetLabel}}</span><strong>${{fmt(item.final_asset)}}</strong></div>
                    ${{withdrawRow}}
                    <div class="card-row"><span>총 수익금</span><span style="color:${{item.total_profit>=0?'#dc2626':'#2563eb'}}; font-weight:600;">${{item.total_profit>=0?'+':''}}${{fmt(item.total_profit)}} (${{item.profit_rate.toFixed(2)}}%)</span></div>
                </div>`;
            }}).join('');
            
            // 월별 요약 테이블 렌더링
            let monthlyData = d.monthly_summary.slice();
            if (document.getElementById('sort-select-monthly').value === 'desc') monthlyData.reverse(); 
            
            document.getElementById('monthly-tbody').innerHTML = monthlyData.map(m => `
                <tr>
                    <td>${{m.기간}}</td>
                    <td>${{Math.floor(m.주당배당).toLocaleString()}}</td>
                    <td style="color:#d97706; font-weight:600;">${{m.배당률.toFixed(2)}}%</td>
                    <td>${{m.배당합계 > 0 ? fmtMan(m.배당합계) : '-'}}</td>
                    <td style="font-weight:600;">${{fmtMan(m.기말자산)}}</td>
                    <td style="color:${{colorForChange(m.증감)}}; font-weight:600;">${{m.증감 > 0 ? '+' : ''}}${{fmtMan(m.증감)}}</td>
                </tr>
            `).join('');
            
            // 상세 거래 내역 테이블 렌더링
            let historyData = d.history.slice();
            if (document.getElementById('sort-select-history').value === 'desc') historyData.reverse(); 
            
            document.getElementById('tbody').innerHTML = historyData.map(h => `
                <tr>
                    <td>${{h.날짜}}</td>
                    <td><span class="badge ${{getBadgeClass(h.구분)}}">${{h.구분}}</span></td>
                    <td style="color:#64748b; font-weight:600;">${{h.종목}}</td>
                    <td>${{fmt(h.단가)}}</td>
                    <td>${{h.수량.toLocaleString()}}</td>
                    <td>${{h.거래금액 > 0 ? fmt(h.거래금액) : '-'}}</td>
                    <td>${{fmt(h.현금잔고)}}</td>
                    <td><strong>${{fmt(h.총자산)}}</strong></td>
                </tr>
            `).join('');
        }}

        new Chart(document.getElementById('assetChart'), {{
            type: 'line', data: {{ labels: labels, datasets: {json.dumps(datasets)} }},
            options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ ticks: {{ callback: v => (v/10000) + '만' }} }} }} }}
        }});
        
        renderTable();
    </script></body></html>
    """
    components.html(html_code, height=2000, scrolling=True)

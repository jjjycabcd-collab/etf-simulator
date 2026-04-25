import sys
import pandas as pd
import time
import json
import datetime
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정 및 상태 초기화
# ==========================================
st.set_page_config(page_title="해외 ETF 백테스트", layout="wide")

# 세션 상태 초기화
if 'show_settings' not in st.session_state:
    st.session_state.show_settings = True
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False
if 'sim_result_data' not in st.session_state:
    st.session_state.sim_result_data = None
if 'display_title' not in st.session_state:
    st.session_state.display_title = ""

# ==========================================
# 함수 정의부 (yfinance 활용)
# ==========================================

@st.cache_data(ttl=86400)
def fetch_stock_name(code):
    """야후 파이낸스에서 종목명 스크래핑"""
    if not code: return ""
    try:
        ticker = yf.Ticker(code)
        name = ticker.info.get('shortName', code)
        return f"{name}({code.upper()})"
    except:
        return code.upper()

def fetch_actual_prices(code, start_date, end_date):
    """야후 파이낸스에서 일별 종가 수집"""
    if not code: return pd.Series(dtype=float)
    try:
        ticker = yf.Ticker(code)
        # 여유 있게 데이터를 가져와서 범위 커버
        df = ticker.history(start=start_date.strftime('%Y-%m-%d'), 
                            end=(end_date + pd.Timedelta(days=10)).strftime('%Y-%m-%d'))
        if df.empty: return pd.Series(dtype=float)
        # 타임존 제거 및 날짜 인덱스화
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close'].dropna()
    except:
        return pd.Series(dtype=float)

def scrape_dividend_data(code, years_tuple):
    """야후 파이낸스에서 배당금 내역 수집"""
    years = list(years_tuple)
    div_map = {y: [{'val':0.0, 'pay_day':15, 'reinv_day':16, 'yield':0.0} for _ in range(12)] for y in years}
    if not code: return div_map
    try:
        ticker = yf.Ticker(code)
        divs = ticker.dividends
        if not divs.empty:
            divs.index = pd.to_datetime(divs.index).tz_localize(None)
            for date, val in divs.items():
                y, m = date.year, date.month
                if y in years:
                    # 미국 ETF 배당락일 기준, 대략 3일 뒤 지급으로 가정 (28일 초과 방지)
                    p_day = min(date.day + 3, 27)
                    r_day = p_day + 1
                    div_map[y][m-1] = {'val': float(val), 'pay_day': p_day, 'reinv_day': r_day, 'yield': 0.0}
    except: pass
    return div_map

def fmt_usd(val):
    if pd.isna(val) or val == 0: return "$0.00"
    return f"${val:,.2f}"

# ==========================================
# UI 영역
# ==========================================
st.title("🌎 해외 ETF 백테스트")

# 설정 다시 보기 버튼
if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 시뮬레이션 설정 다시 하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

# 시뮬레이션 설정 영역
if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 시뮬레이션 설정 (USD 기준)")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 투자금 (달러 $)", "100000")
            period_input = st.text_input("백테스트 기간 (2025 또는 2025.1~2026.1)", "2023~2024")
        with col2:
            etf_input = st.text_input("종목 티커 (쉼표 구분, 예: QQQ, SCHD)", "QQQ, SCHD")
            div_option = st.radio("배당금 처리", ["재투자", "인출(생활비)"], index=0, horizontal=True)
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('미국 주식 데이터 분석 및 백테스트 중...'):
            now = datetime.datetime.now()
            curr_year, curr_month = now.year, now.month
            # 숫자와 소수점만 남기기
            try:
                INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            except:
                INITIAL_CASH = 0.0
            
            def parse_date_str(s, is_end=False):
                if '.' in s: parts = s.split('.'); return int(parts[0]), int(parts[1])
                return int(s), (12 if is_end else 1)
            
            try:
                if '~' in period_input:
                    s_part, e_part = period_input.split('~')
                    start_year, start_month = parse_date_str(s_part.strip())
                    end_year, end_month = parse_date_str(e_part.strip(), True)
                else:
                    start_year, start_month = parse_date_str(period_input.strip())
                    end_year, end_month = parse_date_str(period_input.strip(), True)
            except: start_year, start_month, end_year, end_month = 2023, 1, curr_year, curr_month

            if end_year > curr_year or (end_year == curr_year and end_month > curr_month): end_year, end_month = curr_year, curr_month
            start_ts, end_ts = pd.Timestamp(start_year, start_month, 1), pd.Timestamp(end_year, end_month, 28)
            YEAR_RANGE = list(range(start_year, end_year + 1))
            target_ym = []
            for y in YEAR_RANGE:
                for m in range(1, 13):
                    if y == start_year and m < start_month: continue
                    if y == end_year and m > end_month: break
                    target_ym.append((y, m))

            codes = [c.strip().upper() for c in etf_input.replace(',', ' ').split() if c.strip()]
            K_CODE = codes[0] if codes else ""
            T_CODE = codes[1] if len(codes) > 1 else None
            K_NAME_RAW = fetch_stock_name(K_CODE) if K_CODE else ""
            T_NAME_RAW = fetch_stock_name(T_CODE) if T_CODE else ""
            
            st.session_state.display_title = f"### 📈 {period_input} {K_NAME_RAW}" + (f", {T_NAME_RAW}" if T_CODE else "")
            
            k_prices_all = fetch_actual_prices(K_CODE, start_ts, end_ts)
            t_prices_all = fetch_actual_prices(T_CODE, start_ts, end_ts) if T_CODE else pd.Series(dtype=float, index=pd.to_datetime([]))
            k_divs_all, t_divs_all = scrape_dividend_data(K_CODE, tuple(YEAR_RANGE)), scrape_dividend_data(T_CODE, tuple(YEAR_RANGE)) if T_CODE else {}

            history, cash, k_sh, t_sh, total_div, first_buy = [], INITIAL_CASH, 0, 0, 0.0, False

            def get_safe_price(ps, y, m, d, after=False):
                if ps.empty: return None, None
                target_dt = pd.Timestamp(y, m, d)
                found = ps.index[ps.index > target_dt] if after else ps.index[ps.index >= target_dt]
                if not found.empty and found[0].year == y and found[0].month == m: return (found[0], float(ps.loc[found[0]]))
                return (None, None)

            # --- 시뮬레이션 로직 ---
            for y, m in target_ym:
                k_d = k_divs_all.get(y, [None]*12)[m-1] if K_CODE else None
                t_d = t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
                
                if not T_CODE and K_CODE:
                    if not first_buy:
                        dt, p = get_safe_price(k_prices_all, y, m, 1)
                        if dt: 
                            k_sh = int(cash // p); cash -= (k_sh * p); first_buy = True
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})
                    
                    dt_pay = None
                    if k_sh > 0 and k_d and k_d['val'] > 0:
                        dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                        if dt: 
                            dt_pay = dt; dv = k_sh * k_d['val']; total_div += dv
                            if div_option == "재투자": cash += dv
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0.0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                    
                    if div_option == "재투자" and dt_pay:
                        found = k_prices_all.index[k_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m:
                            dt_re, p_re = found[0], float(k_prices_all.loc[found[0]])
                            if cash >= p_re: 
                                add_sh = int(cash // p_re); cash -= (add_sh * p_re); k_sh += add_sh
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt_re.strftime('%y/%m/%d'),'구분':'재투자','종목':K_CODE,'단가':p_re,'수량':add_sh,'거래금액':add_sh*p_re,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(k_sh*p_re),'배당률':0.0})
                    
                    km = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)]
                    if not km.empty: 
                        history.append({'연도':y,'월':f"{m}월",'날짜':km.index[-1].strftime('%y/%m/%d'),'구분':'평가','종목':K_CODE,'단가':float(km.iloc[-1]),'수량':k_sh,'거래금액':0.0,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(k_sh*float(km.iloc[-1])),'배당률':0.0})
                
                elif T_CODE and K_CODE:
                    # 교체 매매(스윙) 모드도 동일하게 float 단위 적용
                    if t_sh > 0:
                        dt_pay = None
                        if t_d and t_d['val'] > 0:
                            dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                            if dt: 
                                dt_pay = dt; dv = t_sh * t_d['val']; total_div += dv
                                if div_option == "재투자": cash += dv
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':T_CODE,'단가':t_d['val'],'수량':t_sh,'거래금액':0.0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(t_sh*p),'배당률':t_d['yield']})
                        dt_sw = None
                        if dt_pay: 
                            found = t_prices_all.index[t_prices_all.index > dt_pay]
                            dt_sw = found[0] if not found.empty and found[0].year == y and found[0].month == m else None
                        else: 
                            dt_sw, _ = get_safe_price(t_prices_all, y, m, t_d['reinv_day'] if t_d else 18)
                        
                        if dt_sw:
                            p_s = float(t_prices_all.loc[dt_sw]); sell_amt = t_sh * p_s; cash += sell_amt
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매도','종목':T_CODE,'단가':p_s,'수량':t_sh,'거래금액':sell_amt,'수령배당금':0.0,'현금잔고':cash,'총자산':cash,'배당률':0.0})
                            t_sh = 0
                            if dt_sw in k_prices_all.index: 
                                p_k = float(k_prices_all.loc[dt_sw]); k_sh = int(cash // p_k); cash -= (k_sh * p_k)
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p_k,'수량':k_sh,'거래금액':k_sh*p_k,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(k_sh*p_k),'배당률':0.0})
                    
                    if not first_buy:
                        dt, p = get_safe_price(k_prices_all, y, m, 1)
                        if dt: 
                            k_sh = int(cash // p); cash -= (k_sh * p); first_buy = True
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})
                    
                    if k_sh > 0:
                        dt_pay = None
                        if k_d and k_d['val'] > 0:
                            dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                            if dt: 
                                dt_pay = dt; dv = k_sh * k_d['val']; total_div += dv
                                if div_option == "재투자": cash += dv
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0.0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                        
                        dt_sw = None
                        if dt_pay: 
                            found = k_prices_all.index[k_prices_all.index > dt_pay]
                            dt_sw = found[0] if not found.empty and found[0].year == y and found[0].month == m else None
                        else: 
                            dt_sw, _ = get_safe_price(k_prices_all, y, m, k_d['reinv_day'] if k_d else 18)
                        
                        if dt_sw:
                            p_s = float(k_prices_all.loc[dt_sw]); sell_amt = k_sh * p_s; cash += sell_amt
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매도','종목':K_CODE,'단가':p_s,'수량':k_sh,'거래금액':sell_amt,'수령배당금':0.0,'현금잔고':cash,'총자산':cash,'배당률':0.0})
                            k_sh = 0
                            if dt_sw in t_prices_all.index: 
                                p_t = float(t_prices_all.loc[dt_sw]); t_sh = int(cash // p_t); cash -= (t_sh * p_t)
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매수','종목':T_CODE,'단가':p_t,'수량':t_sh,'거래금액':t_sh*p_t,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+(t_sh*p_t),'배당률':0.0})
                    
                    km, tm = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)], t_prices_all[(t_prices_all.index.year == y) & (t_prices_all.index.month == m)]
                    if not km.empty or not tm.empty:
                        cur_t = K_CODE if k_sh > 0 else (T_CODE if t_sh > 0 else "-")
                        cur_s = k_sh if k_sh > 0 else t_sh
                        cur_p = float(km.iloc[-1]) if k_sh > 0 and not km.empty else (float(tm.iloc[-1]) if t_sh > 0 and not tm.empty else 0.0)
                        last_dt = km.index[-1].strftime('%y/%m/%d') if not km.empty else tm.index[-1].strftime('%y/%m/%d')
                        val_total = (k_sh*float(km.iloc[-1]) if not km.empty else 0.0) + (t_sh*float(tm.iloc[-1]) if not tm.empty else 0.0)
                        history.append({'연도':y,'월':f"{m}월",'날짜':last_dt,'구분':'평가','종목':cur_t,'단가':cur_p,'수량':cur_s,'거래금액':0.0,'수령배당금':0.0,'현금잔고':cash,'총자산':cash+val_total,'배당률':0.0})

            df_hist = pd.DataFrame(history)
            monthly_summary, labels, divs, dps_list, assets, prev_asset = [], [], [], [], [], INITIAL_CASH
            
            for y, m in target_ym:
                m_data = df_hist[(df_hist['연도'] == y) & (df_hist['월'] == f"{m}월")]
                if m_data.empty: continue
                m_div = float(m_data['수령배당금'].sum())
                m_final = float(m_data.iloc[-1]['총자산'])
                m_dps = float(m_data[m_data['구분'] == '배당']['단가'].sum())
                m_yld = float(m_data[m_data['구분'] == '배당']['배당률'].sum())
                
                labels.append(f"{y}.{m}"); divs.append(m_div); dps_list.append(m_dps); assets.append(m_final)
                monthly_summary.append({'기간': f"{y}.{m:02d}", '주당배당금': m_dps, '배당률': m_yld, '배당금': m_div, '총자산': m_final, '증감': float(m_final - prev_asset)})
                prev_asset = m_final

            df_sum = pd.DataFrame(monthly_summary)
            json_summary_str = df_sum.to_json(orient='records', force_ascii=False) if not df_sum.empty else "[]"
            json_history_str = df_hist.to_json(orient='records', force_ascii=False) if not df_hist.empty else "[]"

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH, 
                'last_asset': assets[-1] if assets else INITIAL_CASH, 
                'total_div': total_div,
                'json_summary': json_summary_str, 
                'json_history': json_history_str,
                'labels': labels, 'divs': divs, 'dps_list': dps_list, 'assets': assets, 'div_option': div_option
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
    
    total_prof = (res['last_asset'] if res['div_option'] == "재투자" else res['last_asset'] + res['total_div']) - res['initial_cash']
    prof_rate = (total_prof / res['initial_cash']) * 100 if res['initial_cash'] else 0
    prof_col = "#dc2626" if total_prof > 0 else "#2563eb"

    json_summary = res.get('json_summary', "[]")
    json_history = res.get('json_history', "[]")

    html_code = f"""
    <!DOCTYPE html><html><head><meta charset="utf-8"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; }}
        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 25px; }}
        .card {{ background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; }}
        .card h3 {{ font-size: 13px; margin: 0 0 8px 0; color: #64748b; font-weight: 600; }}
        .card p {{ font-size: 16px; margin: 0; word-break: keep-all; font-weight:700; }}
        .section-title {{ font-size: 16px; font-weight: 700; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
        .header-flex {{ display: flex; align-items: center; justify-content: space-between; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
        .header-title {{ font-size: 16px; font-weight: 700; margin: 0; }}
        .sort-select {{ padding: 4px 8px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 12px; background: white; cursor: pointer; outline: none; font-weight: 600; color: #475569; }}
        .table-wrapper {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; min-width: 600px; }}
        th {{ background: #f1f5f9; padding: 12px; font-size: 12px; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; font-size: 12px; }}
        .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; }}
        .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .div {{ background: #10b981; }} .eval {{ background: #94a3b8; }}
        .row-div {{ background-color: #f0fdf4 !important; }} .div-val {{ color: #166534; font-weight: 800; }}
    </style>
    </head><body>
    
    <div class="card-grid">
        <div class="card"><h3>초기 투자금</h3><p style="color:#3b82f6;">{fmt_usd(res['initial_cash'])}</p></div>
        <div class="card"><h3>최종 자산</h3><p style="color:#dc2626;">{fmt_usd(res['last_asset'])}</p></div>
        <div class="card"><h3>누적 배당금</h3><p style="color:#166534;">{fmt_usd(res['total_div'])}</p></div>
        <div class="card"><h3>총 수익금</h3><p style="color:{prof_col};">{fmt_usd(total_prof)}</p></div>
        <div class="card"><h3>총 수익률</h3><p style="color:#dc2626;">{prof_rate:.2f}%</p></div>
    </div>
    
    <div class="section-title">📉 배당금 및 주당 배당금 추이</div>
    <div class="chart-container"><canvas id="divChart"></canvas></div>
    
    <div class="section-title">📈 자산 성장 추이</div>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    
    <div class="header-flex">
        <h3 class="header-title">📅 월별 요약</h3>
        <select class="sort-select" onchange="renderSummary(this.value)">
            <option value="desc" selected>최신순</option>
            <option value="asc">과거순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead>
            <tbody id="summary-tbody"></tbody>
        </table>
    </div>

    <div class="header-flex">
        <h3 class="header-title">🔍 상세 거래 내역</h3>
        <select class="sort-select" onchange="renderHistory(this.value)">
            <option value="asc" selected>과거순</option>
            <option value="desc">최신순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>날짜</th><th>구분</th><th>종목</th><th>단가</th><th>수량</th><th>거래금액</th><th>배당금</th><th>잔고</th><th>총자산</th></tr></thead>
            <tbody id="history-tbody"></tbody>
        </table>
    </div>

    <script>
        const summaryData = {json_summary};
        const historyData = {json_history};

        // 소수점 2자리까지 표시하는 USD 포맷팅 함수
        function fmtUsd(val) {{
            if (val === 0 || val === '0') return "$0.00";
            let num = parseFloat(val);
            return "$" + num.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
        }}

        function getCls(cat) {{
            if (cat.includes("매수") || cat.includes("재투자")) return "buy";
            if (cat.includes("매도")) return "sell";
            if (cat.includes("배당")) return "div";
            return "eval";
        }}

        function renderSummary(order) {{
            let data = [...summaryData]; 
            if (order === 'desc') data.reverse(); 
            
            const tbody = document.getElementById('summary-tbody');
            tbody.innerHTML = data.map(s => {{
                let colorClass = s.증감 > 0 ? '#dc2626' : '#2563eb';
                return `<tr>
                    <td>${{s.기간}}</td>
                    <td>${{fmtUsd(s.주당배당금)}}</td>
                    <td style='color:#f59e0b; font-weight:600;'>${{parseFloat(s.배당률).toFixed(2)}}%</td>
                    <td>${{fmtUsd(s.배당금)}}</td>
                    <td><b>${{fmtUsd(s.총자산)}}</b></td>
                    <td style='color:${{colorClass}}; font-weight:600;'>${{fmtUsd(s.증감)}}</td>
                </tr>`;
            }}).join('');
        }}

        function renderHistory(order) {{
            let data = [...historyData];
            if (order === 'desc') data.reverse();
            
            const tbody = document.getElementById('history-tbody');
            tbody.innerHTML = data.map(r => {{
                let cls = getCls(r.구분);
                let rowCls = cls === 'div' ? " class='row-div'" : "";
                let amt = r.거래금액 > 0 ? fmtUsd(r.거래금액) : '-';
                let div = r.수령배당금 > 0 ? '+' + fmtUsd(r.수령배당금) : '-';
                return `<tr${{rowCls}}>
                    <td>${{r.날짜}}</td>
                    <td><span class='badge ${{cls}}'>${{r.구분}}</span></td>
                    <td style='text-align:center;'>${{r.종목}}</td>
                    <td>${{fmtUsd(r.단가)}}</td>
                    <td>${{Math.floor(r.수량).toLocaleString()}}</td>
                    <td>${{amt}}</td>
                    <td class='div-val'>${{div}}</td>
                    <td>${{fmtUsd(r.현금잔고)}}</td>
                    <td style='font-weight:700;'>${{fmtUsd(r.총자산)}}</td>
                </tr>`;
            }}).join('');
        }}

        renderSummary('desc');
        renderHistory('asc');

        const ctx1 = document.getElementById('divChart');
        new Chart(ctx1, {{ type: 'bar', data: {{ labels: {json.dumps(res['labels'])}, datasets: [{{ label: '배당금($)', data: {json.dumps(res['divs'])}, backgroundColor: '#10b981', yAxisID: 'y' }}, {{ label: '주당 배당금($)', data: {json.dumps(res['dps_list'])}, type: 'line', borderColor: '#f59e0b', yAxisID: 'y1', tension: 0.3 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
        
        const ctx2 = document.getElementById('assetChart');
        new Chart(ctx2, {{ type: 'line', data: {{ labels: {json.dumps(res['labels'])}, datasets: [{{ label: '총자산($)', data: {json.dumps(res['assets'])}, borderColor: '#ef4444', fill: false, tension: 0.1 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
    </script>
    </body></html>
    """
    components.html(html_code, height=2200, scrolling=True)

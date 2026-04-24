import sys
import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
import json
import datetime
import shutil
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정
# ==========================================
st.set_page_config(page_title="ETF 배당 백테스트", layout="wide")

# ==========================================
# API 키 설정
# ==========================================
try:
    KIS_APP_KEY = st.secrets["KIS_APP_KEY"]
    KIS_APP_SECRET = st.secrets["KIS_APP_SECRET"]
except:
    KIS_APP_KEY = "YOUR_APP_KEY_HERE"
    KIS_APP_SECRET = "YOUR_APP_SECRET_HERE"

KIS_TOKEN = None

# ==========================================
# 함수 정의부
# ==========================================
def get_kis_token():
    global KIS_TOKEN
    if KIS_TOKEN: return KIS_TOKEN
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}
    try:
        res = requests.post(url, headers={"content-type": "application/json"}, json=body)
        KIS_TOKEN = res.json().get('access_token')
        return KIS_TOKEN
    except: return None

@st.cache_data(ttl=86400)
def fetch_stock_name(code, token):
    if not code: return ""
    if token:
        url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/search-info"
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "CTPF1002R", "custtype": "P"}
        try:
            res = requests.get(url, headers=headers, params={"PRDT_TYPE_CD": "300", "PDNO": code})
            data = res.json()
            if data['rt_cd'] == '0': return f"{data['output']['prdt_abrv_name']} ({code})"
        except: pass
    return f"종목 ({code})"

# [수정] 실제 시장 종가(Market Price) 수집 및 캐시 강제 갱신
def fetch_actual_prices(code, start_date, end_date, token):
    if not code: return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    
    # 캐시 파일명 변경하여 기존 잘못된 데이터 강제 삭제
    price_file = f"price_market_v2_{code}.json"
    if os.path.exists(price_file):
        try:
            with open(price_file, "r") as f:
                cached = json.load(f)
            series = pd.Series({pd.to_datetime(k): v for k, v in cached.items()}).sort_index()
            if not series.empty and series.index[0] <= start_date: return series
        except: pass

    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST03010100", "custtype": "P"}
    all_prices, s_dt, e_dt = {}, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')
    current_end = e_dt
    
    while True:
        # FID_ORG_ADJ_PRC: "0" (반드시 실제 주가로 가져옴)
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": s_dt, "FID_INPUT_DATE_2": current_end, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"} 
        try:
            res = requests.get(url, headers=headers, params=params)
            data = res.json()
            if data['rt_cd'] != '0' or not data.get('output2'): break
            for row in data['output2']:
                if row['stck_bsop_date']:
                    all_prices[pd.to_datetime(row['stck_bsop_date'])] = int(row['stck_clpr'])
            oldest = data['output2'][-1]['stck_bsop_date']
            if oldest <= s_dt or len(data['output2']) < 100: break
            current_end = (pd.to_datetime(oldest) - pd.Timedelta(days=1)).strftime('%Y%m%d')
        except: break
    
    price_series = pd.Series(all_prices).sort_index()
    try:
        with open(price_file, "w") as f:
            json.dump({k.strftime('%Y-%m-%d'): v for k, v in all_prices.items()}, f)
    except: pass
    return price_series

def scrape_dividend_data(code, years_tuple):
    years = list(years_tuple)
    file_path = f"dividend_data_{code}.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            parsed_cache = {int(k): v for k, v in cached_data.items()}
            if all(y in parsed_cache for y in years): return parsed_cache
        except: pass
    div_map = {y: [{'val':0, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)] for y in years}
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
        chromedriver_path = shutil.which("chromedriver")
        driver = webdriver.Chrome(service=Service(chromedriver_path if chromedriver_path else ChromeDriverManager().install()), options=options)
        driver.get(f"https://www.etfcheck.co.kr/mobile/etpitem/{code}/cash/hist")
        time.sleep(5)
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for row in soup.find_all('tr'):
            tds = row.find_all('td')
            if len(tds) >= 2:
                try:
                    ex_date = pd.to_datetime(tds[0].text.strip())
                    div_val = int(re.sub(r'[^0-9]', '', tds[1].text.strip()))
                    div_yield_val = float(re.sub(r'[^0-9.]', '', tds[2].text.strip())) if len(tds) >= 3 else 0.0
                    pay_dt = ex_date + pd.DateOffset(months=1) if ex_date.day > 16 else ex_date
                    p_day, r_day = (2, 3) if ex_date.day > 16 else (17, 18)
                    if pay_dt.year in years:
                        div_map[pay_dt.year][pay_dt.month-1] = {'val': div_val, 'pay_day': p_day, 'reinv_day': r_day, 'yield': div_yield_val}
                except: pass
    except: pass
    finally:
        if driver: driver.quit()
    for y in years:
        if not any(item['val'] > 0 for item in div_map[y]):
            if code == '498400': div_map[y] = [{'val':230, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)]
            elif code == '472150': div_map[y] = [{'val':250, 'pay_day':2, 'reinv_day':3, 'yield':0.0} for _ in range(12)]
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(div_map, f, ensure_ascii=False, indent=4)
    except: pass
    return div_map

def fmt_man(val):
    if val == 0: return "0"
    return f"{int(val) // 10000:,}만" if abs(val) >= 10000 else f"{int(val):,}"

# ==========================================
# UI 영역: 사이드바 입력
# ==========================================
with st.sidebar:
    st.header("⚙️ 시뮬레이션 설정")
    cash_input = st.text_input("초기 투자금 (원)", "40000000")
    period_input = st.text_input("테스트 기간 (예: 2025.1~2026)", "2025.1~2026.4")
    etf_input = st.text_input("종목 코드 (쉼표 구분)", "498400, 472150")
    run_btn = st.button("시뮬레이션 실행", type="primary")

if not run_btn:
    st.title("📊 ETF 배당 시뮬레이터")

# ==========================================
# 실행 영역
# ==========================================
if run_btn:
    with st.spinner('실시간 시장가 데이터를 수집하며 백테스트 중입니다...'):
        now = datetime.datetime.now()
        curr_year, curr_month = now.year, now.month
        INITIAL_CASH = int(re.sub(r'[^0-9]', '', cash_input))
        
        def parse_date_str(s, is_end=False):
            if '.' in s:
                parts = s.split('.'); return int(parts[0]), int(parts[1])
            return int(s), (12 if is_end else 1)
        
        try:
            if '~' in period_input:
                s_part, e_part = period_input.split('~')
                start_year, start_month = parse_date_str(s_part.strip())
                end_year, end_month = parse_date_str(e_part.strip(), True)
            else:
                start_year, start_month = parse_date_str(period_input.strip())
                end_year, end_month = parse_date_str(period_input.strip(), True)
        except:
            start_year, start_month, end_year, end_month = 2025, 1, curr_year, curr_month

        if end_year > curr_year or (end_year == curr_year and end_month > curr_month):
            end_year, end_month = curr_year, curr_month
        
        start_ts = pd.Timestamp(start_year, start_month, 1)
        end_ts = pd.Timestamp(end_year, end_month, 28)
        YEAR_RANGE = list(range(start_year, end_year + 1))
        target_ym = []
        for y in YEAR_RANGE:
            for m in range(1, 13):
                if y == start_year and m < start_month: continue
                if y == end_year and m > end_month: break
                target_ym.append((y, m))

        KIS_TOKEN = get_kis_token()
        codes = [c.strip() for c in etf_input.replace(',', ' ').split() if c.strip().isdigit()]
        K_CODE = codes[0]; T_CODE = codes[1] if len(codes) > 1 else None
        K_NAME_RAW = fetch_stock_name(K_CODE, KIS_TOKEN)
        T_NAME_RAW = fetch_stock_name(T_CODE, KIS_TOKEN) if T_CODE else ""
        
        # [수정] 상단 타이틀 형식 반영
        display_name = f"{K_NAME_RAW.split(' (')[0]}"
        if T_CODE: display_name += f", {T_NAME_RAW.split(' (')[0]}"
        st.title(f"📊 {period_input} {display_name} ({', '.join(codes)}) 백테스트 리포트")
        
        k_prices_all = fetch_actual_prices(K_CODE, start_ts, end_ts, KIS_TOKEN)
        t_prices_all = fetch_actual_prices(T_CODE, start_ts, end_ts, KIS_TOKEN) if T_CODE else pd.Series(dtype=float)
        k_divs_all = scrape_dividend_data(K_CODE, tuple(YEAR_RANGE))
        t_divs_all = scrape_dividend_data(T_CODE, tuple(YEAR_RANGE)) if T_CODE else {}

        history, cash, k_sh, t_sh, total_div, first_buy = [], INITIAL_CASH, 0, 0, 0, False

        def get_safe_price(ps, y, m, d):
            if ps.empty: return None, None
            target_dt = pd.Timestamp(y, m, d)
            found = ps.index[ps.index >= target_dt]
            if not found.empty and found[0].year == y and found[0].month == m:
                return (found[0], int(ps.loc[found[0]]))
            return (None, None)

        for y, m in target_ym:
            k_d, t_d = k_divs_all[y][m-1], t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
            
            # TIGER(T) 매도 및 KODEX(K) 매수 (SWING)
            if T_CODE and t_sh > 0:
                if t_d['val'] > 0:
                    dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                    if dt:
                        dv = t_sh * t_d['val']; cash += dv; total_div += dv
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':T_CODE,'단가':t_d['val'],'수량':t_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(t_sh*p),'배당률':t_d['yield']})
                dt_s, p_s = get_safe_price(t_prices_all, y, m, t_d['reinv_day'])
                if dt_s:
                    sell_amt = t_sh * p_s; cash += sell_amt
                    history.append({'연도':y,'월':f"{m}월",'날짜':dt_s.strftime('%y/%m/%d'),'구분':'매도','종목':T_CODE,'단가':p_s,'수량':t_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); t_sh = 0
                    dt_k, p_k = get_safe_price(k_prices_all, dt_s.year, dt_s.month, dt_s.day)
                    if dt_k:
                        k_sh = cash // p_k; cash -= (k_sh*p_k)
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt_k.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p_k,'수량':k_sh,'거래금액':k_sh*p_k,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_k),'배당률':0.0})

            # 초기 매수 (첫 달)
            if not first_buy:
                dt, p = get_safe_price(k_prices_all, y, m, 1)
                if dt:
                    k_sh = cash // p; cash -= (k_sh*p); first_buy = True
                    history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})

            # KODEX(K) 매도 및 TIGER(T) 매수
            if k_sh > 0:
                if k_d['val'] > 0:
                    dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                    if dt:
                        dv = k_sh * k_d['val']; cash += dv; total_div += dv
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                if T_CODE:
                    dt_s, p_s = get_safe_price(k_prices_all, y, m, k_d['reinv_day'])
                    if dt_s:
                        sell_amt = k_sh * p_s; cash += sell_amt
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt_s.strftime('%y/%m/%d'),'구분':'매도','종목':K_CODE,'단가':p_s,'수량':k_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); k_sh = 0
                        dt_t, p_t = get_safe_price(t_prices_all, dt_s.year, dt_s.month, dt_s.day)
                        if dt_t:
                            t_sh = cash // p_t; cash -= (t_sh*p_t)
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt_t.strftime('%y/%m/%d'),'구분':'매수','종목':T_CODE,'단가':p_t,'수량':t_sh,'거래금액':t_sh*p_t,'수령배당금':0,'현금잔고':cash,'총자산':cash+(t_sh*p_t),'배당률':0.0})

            # 월말 평가
            k_m_prices = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)]
            t_m_prices = t_prices_all[(t_prices_all.index.year == y) & (t_prices_all.index.month == m)] if T_CODE else pd.Series()
            if not k_m_prices.empty or not t_m_prices.empty:
                cur_ticker = K_CODE if k_sh > 0 else (T_CODE if t_sh > 0 else "-")
                cur_sh = k_sh if k_sh > 0 else t_sh
                cur_p = int(k_m_prices.iloc[-1]) if k_sh > 0 and not k_m_prices.empty else (int(t_m_prices.iloc[-1]) if t_sh > 0 and not t_m_prices.empty else 0)
                last_dt = k_m_prices.index[-1] if not k_m_prices.empty else t_m_prices.index[-1]
                v_k = k_sh * (int(k_m_prices.iloc[-1]) if not k_m_prices.empty else 0)
                v_t = t_sh * (int(t_m_prices.iloc[-1]) if not t_m_prices.empty else 0)
                history.append({'연도':y,'월':f"{m}월",'날짜':last_dt.strftime('%y/%m/%d'),'구분':'평가','종목':cur_ticker,'단가':cur_p,'수량':cur_sh,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+v_k+v_t,'배당률':0.0})

        df_hist = pd.DataFrame(history)
        monthly_summary, labels, divs, dps_list, assets, prev_asset = [], [], [], [], [], INITIAL_CASH
        for y, m in target_ym:
            m_data = df_hist[(df_hist['연도'] == y) & (df_hist['월'] == f"{m}월")]
            if m_data.empty: continue
            m_div = m_data['수령배당금'].sum(); m_final = m_data.iloc[-1]['총자산']
            m_dps = m_data[m_data['구분'] == '배당']['단가'].sum(); m_yield = m_data[m_data['구분'] == '배당']['배당률'].sum()
            labels.append(f"{y}.{m}"); divs.append(int(m_div)); dps_list.append(int(m_dps)); assets.append(int(m_final))
            monthly_summary.append({'기간': f"{y}.{m:02d}", '주당배당금': m_dps, '배당률': m_yield, '배당금': m_div, '총자산': m_final, '증감': m_final - prev_asset})
            prev_asset = m_final

        total_profit = assets[-1] - INITIAL_CASH
        profit_color = "#dc2626" if total_profit > 0 else "#2563eb"

        summary_rows = "".join([f"<tr><td>{s['기간']}</td><td>{int(s['주당배당금']):,}</td><td style='color:#f59e0b; font-weight:600;'>{s['배당률']:.2f}%</td><td>{fmt_man(s['배당금'])}</td><td><b>{fmt_man(s['총자산'])}</b></td><td style='color:{'#dc2626' if s['증감']>0 else '#2563eb'}; font-weight:600;'>{fmt_man(s['증감'])}</td></tr>" for s in monthly_summary[::-1]])
        def get_cls(cat): return "buy" if "매수" in cat else "sell" if "매도" in cat else "div" if "배당" in cat else "eval"
        df_display = df_hist.sort_values(by=['날짜'], ascending=True)
        detailed_rows = "".join([f"<tr class='row-{get_cls(r['구분'])}'><td>{r['날짜']}</td><td><span class='badge {get_cls(r['구분'])}'>{r['구분']}</span></td><td style='text-align:center;'>{r['종목']}</td><td>{r['단가']:,}</td><td>{r['수량']:,}</td><td>{fmt_man(r['거래금액']) if r['거래금액']>0 else '-'}</td><td class='div-val'>{f'+{fmt_man(r['수령배당금'])}' if r['수령배당금']>0 else '-'}</td><td>{fmt_man(r['현금잔고'])}</td><td style='font-weight:700;'>{fmt_man(r['총자산'])}</td></tr>" for _, r in df_display.iterrows()])

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; }}
                .card-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 25px; }}
                .card {{ background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; }}
                .card h3 {{ font-size: 13px; margin: 0 0 8px 0; color: #64748b; font-weight: 600; }}
                .card p {{ font-size: 16px; margin: 0; word-break: keep-all; }}
                .section-title {{ font-size: 16px; font-weight: 700; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
                .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 280px; margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; }}
                th {{ background: #f1f5f9; padding: 12px; font-size: 12px; }}
                td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; font-size: 12px; }}
                .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; }}
                .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .div {{ background: #10b981; }} .eval {{ background: #94a3b8; }}
                .row-div {{ background-color: #f0fdf4 !important; }} .div-val {{ color: #166534; font-weight: 800; }}
                .note-box {{ margin-top: 15px; padding: 10px; font-size: 13px; color: #64748b; line-height: 1.6; }}
                @media (max-width: 768px) {{ .card-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
            </style>
        </head>
        <body>
            <div class="card-grid">
                <div class="card"><h3>초기 투자금</h3><p style="color:#3b82f6; font-weight:700;">{fmt_man(INITIAL_CASH)}원</p></div>
                <div class="card"><h3>최종 자산</h3><p style="color:#dc2626; font-weight:700;">{fmt_man(assets[-1])}원</p></div>
                <div class="card"><h3>누적 배당금</h3><p style="color:#166534; font-weight:700;">{fmt_man(int(total_div))}원</p></div>
                <div class="card"><h3>총 수익금</h3><p style="color:{profit_color}; font-weight:700;">{fmt_man(total_profit)}원</p></div>
                <div class="card"><h3>총 수익률</h3><p style="color:#dc2626; font-weight:700;">{((assets[-1]/INITIAL_CASH)-1)*100:.2f}%</p></div>
            </div>
            <div class="section-title">📉 배당금 및 주당 배당금 추이</div>
            <div class="chart-container"><canvas id="divChart"></canvas></div>
            <div class="section-title">📈 자산 성장 추이</div>
            <div class="chart-container"><canvas id="assetChart"></canvas></div>
            <div class="section-title">📅 월별 요약 (최신순)</div>
            <table><thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead><tbody>{summary_rows}</tbody></table>
            <div class="section-title">🔍 상세 거래 내역 (과거순)</div>
            <table><thead><tr><th>날짜</th><th>구분</th><th>종목</th><th>단가</th><th>수량</th><th>거래금액</th><th>배당금</th><th>잔고</th><th>총자산</th></tr></thead><tbody>{detailed_rows}</tbody></table>
            <div class="note-box">
                ※ 재투자는 받은 배당금을 그 다음 거래일에 매매하는 걸로 가정했습니다.<br>
                ※ 월말평가는 당월 마지막 거래일 종가와 수량을 계산한 값입니다.
            </div>
            <script>
                new Chart(document.getElementById('divChart'), {{ type: 'bar', data: {{ labels: {json.dumps(labels)}, datasets: [{{ label: '배당금(원)', data: {json.dumps(divs)}, backgroundColor: '#10b981', yAxisID: 'y' }}, {{ label: '주당 배당금(원)', data: {json.dumps(dps_list)}, type: 'line', borderColor: '#f59e0b', yAxisID: 'y1', tension: 0.3 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
                new Chart(document.getElementById('assetChart'), {{ type: 'line', data: {{ labels: {json.dumps(labels)}, datasets: [{{ label: '총자산(원)', data: {json.dumps(assets)}, borderColor: '#ef4444', fill: false, tension: 0.1 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
            </script>
        </body>
        </html>
        """
        components.html(html_template, height=2200, scrolling=True)

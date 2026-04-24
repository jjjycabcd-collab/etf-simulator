import sys
import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
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
st.title("📊 ETF 배당 시뮬레이터")

# ==========================================
# [중요] 보안을 위해 실제 키는 삭제하고 
# Streamlit Secrets에서 불러오는 방식으로 수정
# ==========================================
try:
    KIS_APP_KEY = st.secrets["KIS_APP_KEY"]
    KIS_APP_SECRET = st.secrets["KIS_APP_SECRET"]
except:
    KIS_APP_KEY = "YOUR_APP_KEY_HERE"
    KIS_APP_SECRET = "YOUR_APP_SECRET_HERE"

KIS_TOKEN = None  # <-- 이렇게 왼쪽 끝으로 딱 붙여서 빼주세요!

# ==========================================
# 함수 정의부 (기존 로직)
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
    try:
        r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}", headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(r.text, 'html.parser')
        return f"{soup.select_one('.wrap_company h2 a').text} ({code})"
    except: return f"종목 ({code})"

def fetch_kis_prices(code, start_year, end_year, token):
    if not code: return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST03010100", "custtype": "P"}
    all_prices, start_dt, current_end = {}, f"{start_year}0101", f"{end_year}1231"
    while True:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": current_end, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "1"}
        try:
            res = requests.get(url, headers=headers, params=params)
            data = res.json()
            if data['rt_cd'] != '0' or not data.get('output2'): break
            for row in data['output2']:
                if row['stck_bsop_date']: all_prices[pd.to_datetime(row['stck_bsop_date'])] = int(row['stck_clpr'])
            oldest = data['output2'][-1]['stck_bsop_date']
            if oldest <= start_dt or len(data['output2']) < 100: break
            current_end = (pd.to_datetime(oldest) - pd.Timedelta(days=1)).strftime('%Y%m%d')
        except: break
    return pd.Series(all_prices).sort_index()

def fetch_all_years_data(code, years, token, is_etf=True):
    empty_series = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    if not code: return empty_series, {y: [{'val':0, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)] for y in years}
    
    prices = fetch_kis_prices(code, min(years), max(years), token) if token else empty_series
    if prices.empty: prices = empty_series
    
    div_map = {y: [{'val':0, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)] for y in years}
    if not is_etf: return prices, div_map
    
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(f"https://www.etfcheck.co.kr/mobile/etpitem/{code}/cash/hist")
        time.sleep(3)
        
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for row in soup.find_all('tr'):
            tds = row.find_all('td')
            if len(tds) >= 2:
                try:
                    ex_date = pd.to_datetime(tds[0].text.strip())
                    div_val = int(re.sub(r'[^0-9]', '', tds[1].text.strip()))
                    
                    div_yield_val = 0.0
                    if len(tds) >= 3:
                        try:
                            yield_str = re.sub(r'[^0-9.]', '', tds[2].text.strip())
                            if yield_str: div_yield_val = float(yield_str)
                        except: pass

                    if ex_date.day > 16:
                        pay_dt = ex_date + pd.DateOffset(months=1)
                        p_day, r_day = 2, 3
                    else:
                        pay_dt = ex_date
                        p_day, r_day = 17, 18
                    
                    if pay_dt.year in years:
                        div_map[pay_dt.year][pay_dt.month-1] = {'val': div_val, 'pay_day': p_day, 'reinv_day': r_day, 'yield': div_yield_val}
                except: pass
                
        for y in years:
            y_has_data = any(item['val'] > 0 for item in div_map[y])
            if not y_has_data:
                if code == '498400': div_map[y] = [{'val':230, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)]
                elif code == '472150': div_map[y] = [{'val':250, 'pay_day':2, 'reinv_day':3, 'yield':0.0} for _ in range(12)]
    except: pass
    finally:
        if driver: driver.quit()
    return prices, div_map

def fmt_man(val):
    if val == 0: return "0"
    if abs(val) >= 10000:
        return f"{int(val) // 10000:,}만"
    return f"{int(val):,}"

# ==========================================
# UI 영역: 사이드바 입력
# ==========================================
with st.sidebar:
    st.header("⚙️ 시뮬레이션 설정")
    cash_input = st.text_input("초기 투자금 (원)", "40000000")
    period_input = st.text_input("테스트 기간 (예: 2025~2026)", "2025~2026")
    etf_input = st.text_input("종목 코드 (쉼표 구분)", "498400, 472150")
    run_btn = st.button("시뮬레이션 실행", type="primary")

# ==========================================
# 실행 영역
# ==========================================
if run_btn:
    with st.spinner('실시간 데이터를 수집하며 백테스트를 진행 중입니다... (약 10~20초 소요)'):
        
        # 1. 입력값 파싱
        try:
            INITIAL_CASH = int(re.sub(r'[^0-9]', '', cash_input))
            if INITIAL_CASH == 0: INITIAL_CASH = 40000000
        except:
            INITIAL_CASH = 40000000

        now = datetime.datetime.now()
        curr_year, curr_month = now.year, now.month

        def parse_ym(ym_str):
            parts = ym_str.split('.')
            y = int(parts[0])
            return (y, int(parts[1]), True) if len(parts) > 1 else (y, None, False)

        try:
            if '~' in period_input:
                start_str, end_str = period_input.split('~')
                start_y, start_m, start_has_m = parse_ym(start_str.strip())
                end_y, end_m, end_has_m = parse_ym(end_str.strip())
                start_year, start_month = start_y, (start_m if start_has_m else 1)
                end_year, end_month = end_y, (end_m if end_has_m else 12)
            else:
                y, m, has_m = parse_ym(period_input.strip())
                start_year, start_month = y, (m if has_m else 1)
                end_year, end_month = y, (m if has_m else 12)
        except:
            start_year, start_month, end_year, end_month = 2025, 1, curr_year, curr_month

        if end_year > curr_year or (end_year == curr_year and end_month > curr_month):
            end_year, end_month = curr_year, curr_month
        if start_year > end_year or (start_year == end_year and start_month > end_month):
            start_year, start_month = end_year, end_month

        YEAR_RANGE = list(range(start_year, end_year + 1))
        target_ym = [(y, m) for y in YEAR_RANGE for m in range(1, 13) if not ((y == start_year and m < start_month) or (y == end_year and m > end_month))]

        codes = [c.strip() for c in etf_input.replace(',', ' ').split() if c.strip().isdigit()]
        codes_str = ", ".join(codes) if codes else "498400, 472150"

        if len(codes) >= 2:
            RUN_MODE, K_CODE, T_CODE = 'SWING', codes[0], codes[1]
        elif len(codes) == 1:
            RUN_MODE, K_CODE, T_CODE = 'SINGLE', codes[0], None
        else:
            RUN_MODE, K_CODE, T_CODE = 'SWING', '498400', '472150'

        # 2. 데이터 수집
        KIS_TOKEN = get_kis_token()
        K_NAME = fetch_stock_name(K_CODE, KIS_TOKEN)
        T_NAME = fetch_stock_name(T_CODE, KIS_TOKEN) if T_CODE else ""
        ETF_BRANDS = ['KODEX', 'TIGER', 'ACE', 'SOL', 'RISE', 'PLUS', 'ARIRANG', 'KOSEF', 'HANARO', 'KBSTAR', 'TIMEFOLIO', 'TREX', '마이티', 'HK', '히어로즈']
        K_IS_ETF = any(brand in K_NAME.upper() for brand in ETF_BRANDS)
        T_IS_ETF = any(brand in T_NAME.upper() for brand in ETF_BRANDS) if T_NAME else False

        k_prices_all, k_divs_all = fetch_all_years_data(K_CODE, YEAR_RANGE, KIS_TOKEN, K_IS_ETF)
        t_prices_all, t_divs_all = fetch_all_years_data(T_CODE, YEAR_RANGE, KIS_TOKEN, T_IS_ETF)

        # 3. 시뮬레이션
        history, cash, k_sh, t_sh, total_div, first_buy_done = [], INITIAL_CASH, 0, 0, 0, False

        def log(y, m, d, cat, tick_code, p, sh, trans, div, c, s_val, yld=0.0):
            short_cat = cat.replace("초기매수", "매수").replace("월말평가", "평가")
            history.append({
                '연도': y, '월': f"{m}월", '날짜': d.strftime('%y/%m/%d'), 
                '구분': short_cat, '종목': tick_code, '단가': p, '수량': sh, 
                '거래금액': trans, '수령배당금': div, '현금잔고': c, '총자산': c + s_val, '배당률': yld
            })

        for y, m in target_ym:
            k_p = k_prices_all[k_prices_all.index.year == y] if isinstance(k_prices_all.index, pd.DatetimeIndex) else k_prices_all
            t_p = t_prices_all[t_prices_all.index.year == y] if isinstance(t_prices_all.index, pd.DatetimeIndex) else t_prices_all
            
            k_d = k_divs_all[y][m-1]
            t_d = t_divs_all[y][m-1] if T_CODE else None
            
            def get_d(ps, dy):
                if ps.empty or not isinstance(ps.index, pd.DatetimeIndex): return None
                f = ps.index[ps.index >= pd.Timestamp(y, m, dy)]
                return f[0] if not f.empty and f[0].month == m else None

            if RUN_MODE == 'SINGLE':
                if not first_buy_done:
                    d_buy = get_d(k_p, 1)
                    if d_buy:
                        p = int(k_p.loc[d_buy]); k_sh = cash // p; cash -= (k_sh*p); first_buy_done = True
                        log(y, m, d_buy, "매수", K_CODE, p, k_sh, k_sh*p, 0, cash, k_sh*p)
                
                d_pay = get_d(k_p, k_d['pay_day'])
                if d_pay and k_sh > 0 and k_d['val'] > 0:
                    dv = k_sh * k_d['val']; cash += dv; total_div += dv
                    log(y, m, d_pay, "배당", K_CODE, k_d['val'], k_sh, 0, dv, cash, k_sh*int(k_p.loc[d_pay]), k_d['yield'])
                    
                d_reinv = get_d(k_p, k_d['reinv_day'])
                if d_reinv and d_pay and d_reinv <= d_pay:
                    f = k_p.index[k_p.index > d_pay]
                    d_reinv = f[0] if not f.empty and f[0].month == m else d_pay

                if d_reinv and cash >= (int(k_p.loc[d_reinv]) if not k_p.empty else 9e9):
                    p = int(k_p.loc[d_reinv]); add = cash // p
                    if add > 0:
                        cash -= (add*p); k_sh += add
                        log(y, m, d_reinv, "재투자", K_CODE, p, add, add*p, 0, cash, k_sh*p)
                
                d_last = k_p[k_p.index.month == m].index[-1] if (not k_p.empty and isinstance(k_p.index, pd.DatetimeIndex) and not k_p[k_p.index.month == m].empty) else None
                if d_last: log(y, m, d_last, "평가", K_CODE, int(k_p.loc[d_last]), k_sh, 0, 0, cash, k_sh*int(k_p.loc[d_last]))

            elif RUN_MODE == 'SWING':
                d_t_pay = get_d(t_p, t_d['pay_day']) if t_d else None
                if d_t_pay and t_sh > 0 and t_d['val'] > 0:
                    dv = t_sh * t_d['val']; cash += dv; total_div += dv
                    log(y, m, d_t_pay, "배당", T_CODE, t_d['val'], t_sh, 0, dv, cash, t_sh*int(t_p.loc[d_t_pay]), t_d['yield'])

                d_t_sell = get_d(t_p, t_d['reinv_day']) if t_d else None
                if d_t_sell and d_t_pay and d_t_sell <= d_t_pay:
                    f = t_p.index[t_p.index > d_t_pay]
                    d_t_sell = f[0] if not f.empty and f[0].month == m else d_t_pay

                if d_t_sell and t_sh > 0:
                    p = int(t_p.loc[d_t_sell]); sell = t_sh * p; cash += sell
                    log(y, m, d_t_sell, "매도", T_CODE, p, t_sh, sell, 0, cash, 0); t_sh = 0
                    
                d15 = get_d(k_p, 15)
                if d15 and cash >= (int(k_p.loc[d15]) if not k_p.empty else 9e9):
                    p = int(k_p.loc[d15]); k_sh = cash // p; cash -= (k_sh*p)
                    log(y, m, d15, "매수", K_CODE, p, k_sh, k_sh*p, 0, cash, k_sh*p)
                    
                d_k_pay = get_d(k_p, k_d['pay_day'])
                if d_k_pay and k_sh > 0 and k_d['val'] > 0:
                    dv = k_sh * k_d['val']; cash += dv; total_div += dv
                    log(y, m, d_k_pay, "배당", K_CODE, k_d['val'], k_sh, 0, dv, cash, k_sh*int(k_p.loc[d_k_pay]), k_d['yield'])

                d_k_sell = get_d(k_p, k_d['reinv_day'])
                if d_k_sell and d_k_pay and d_k_sell <= d_k_pay:
                    f = k_p.index[k_p.index > d_k_pay]
                    d_k_sell = f[0] if not f.empty and f[0].month == m else d_k_pay

                if d_k_sell and k_sh > 0:
                    p = int(k_p.loc[d_k_sell]); sell = k_sh * p; cash += sell
                    log(y, m, d_k_sell, "매도", K_CODE, p, k_sh, sell, 0, cash, 0); k_sh = 0
                    
                    p_t = int(t_p.loc[d_k_sell]) if not t_p[t_p.index >= d_k_sell].empty else 0
                    if p_t > 0:
                        t_sh = cash // p_t; cash -= (t_sh*p_t)
                        log(y, m, d_k_sell, "매수", T_CODE, p_t, t_sh, t_sh*p_t, 0, cash, t_sh*p_t)
                
                d_last = k_p[k_p.index.month == m].index[-1] if (not k_p.empty and isinstance(k_p.index, pd.DatetimeIndex) and not k_p[k_p.index.month == m].empty) else None
                if d_last:
                    if t_sh > 0: p = int(t_p.loc[d_last]); log(y, m, d_last, "평가", T_CODE, p, t_sh, 0, 0, cash, t_sh*p)
                    elif k_sh > 0: p = int(k_p.loc[d_last]); log(y, m, d_last, "평가", K_CODE, p, k_sh, 0, 0, cash, k_sh*p)

        # 4. 리포트 생성 및 화면 출력
        df_hist = pd.DataFrame(history)

        if df_hist.empty:
            df_hist = pd.DataFrame(columns=['연도', '월', '날짜', '구분', '종목', '단가', '수량', '거래금액', '수령배당금', '현금잔고', '총자산', '배당률'])
        elif '배당률' not in df_hist.columns:
            df_hist['배당률'] = 0.0

        monthly_summary, labels, divs, dps_list, assets, prev_asset = [], [], [], [], [], INITIAL_CASH
        for y, m in target_ym:
            m_data = df_hist[(df_hist['연도'] == y) & (df_hist['월'] == f"{m}월")]
            m_div = m_data['수령배당금'].sum() if not m_data.empty else 0
            m_final = m_data.iloc[-1]['총자산'] if not m_data.empty else prev_asset
            m_dps = m_data[m_data['구분'] == '배당']['단가'].sum() if not m_data.empty else 0
            m_yield = m_data[m_data['구분'] == '배당']['배당률'].sum() if not m_data.empty else 0.0
            
            labels.append(f"{y}.{m}"); divs.append(int(m_div)); dps_list.append(int(m_dps)); assets.append(int(m_final))
            monthly_summary.append({'기간': f"{y}.{m:02d}", '주당배당금': m_dps, '배당률': m_yield, '배당금': m_div, '총자산': m_final, '증감': m_final - prev_asset})
            prev_asset = m_final

        summary_rows = "".join([f"<tr><td>{s['기간']}</td><td>{int(s['주당배당금']):,}</td><td style='color:#f59e0b; font-weight:600;'>{s['배당률']:.2f}%</td><td>{fmt_man(s['배당금'])}</td><td><b>{fmt_man(s['총자산'])}</b></td><td style='color:{'#dc2626' if s['증감']>0 else '#2563eb'}; font-weight:600;'>{fmt_man(s['증감'])}</td></tr>" for s in monthly_summary])

        def get_cls(cat): return "buy" if "매수" in cat or "재투자" in cat else "sell" if "매도" in cat else "div" if "배당" in cat else "eval"

        detailed_rows = "".join([f"<tr class='row-{get_cls(r['구분'])}'><td>{r['날짜']}</td><td><span class='badge {get_cls(r['구분'])}'>{r['구분']}</span></td><td style='text-align:center;'>{r['종목']}</td><td>{r['단가']:,}</td><td>{r['수량']:,}</td><td>{fmt_man(r['거래금액']) if r['거래금액']>0 else '-'}</td><td class='div-val'>{f'+{fmt_man(r['수령배당금'])}' if r['수령배당금']>0 else '-'}</td><td>{fmt_man(r['현금잔고'])}</td><td style='font-weight:700;'>{fmt_man(r['총자산'])}</td></tr>" for _, r in df_hist.iterrows()])

        title_name = f"{K_NAME.split(' (')[0]} ({codes_str})"
        if RUN_MODE == 'SWING': title_name = f"{K_NAME.split(' (')[0]}, {T_NAME.split(' (')[0]} ({codes_str})"

        html_template = f"""
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; margin: 0; }}
                .container {{ max-width: 1100px; margin: auto; padding-bottom: 30px; }}
                h1 {{ font-size: 22px; text-align: center; margin: 20px 0; display: none; }} /* 스트림릿 타이틀이 있으니 HTML 타이틀은 숨김 */
                
                .card-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 25px; }}
                .card {{ background: white; padding: 15px 10px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; }}
                .card h3 {{ font-size: 13px; margin: 0 0 8px 0; color: #64748b; font-weight: 600; }}
                .card p {{ font-size: 16px; margin: 0; word-break: keep-all; }}
                
                .section-title {{ font-size: 16px; font-weight: 700; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
                
                .chart-container {{ background: white; padding: 15px 10px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; width: 100%; height: 280px; position: relative; box-sizing: border-box; }}
                
                .table-responsive {{ background: white; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; min-width: 100%; white-space: nowrap; }}
                th {{ background: #f1f5f9; padding: 12px 6px; font-size: 12px; }}
                td {{ padding: 10px 6px; border-bottom: 1px solid #f1f5f9; text-align: center; font-size: 12px; }}
                
                .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; }}
                .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .div {{ background: #10b981; }} .eval {{ background: #94a3b8; }}
                .row-div {{ background-color: #f0fdf4; }} .div-val {{ color: #166534; font-weight: 800; }}
                .row-eval td {{ color: #64748b; }}
                .note-text {{ font-size: 12px; color: #64748b; margin: -5px 0 15px 5px; line-height: 1.5; word-break: keep-all; }}

                @media (max-width: 768px) {{
                    .card-grid {{ grid-template-columns: repeat(2, 1fr); }}
                    th, td {{ font-size: 11px; padding: 8px 4px; }}
                    .badge {{ font-size: 10px; padding: 3px 4px; }}
                }}
            </style>
        </head>
        <body>
        <div class="container">
            <h1>📊 {period_input} {title_name} 리포트</h1>
            <div class="card-grid">
                <div class="card"><h3>초기 투자금</h3><p style="color:#3b82f6; font-weight:700;">{fmt_man(INITIAL_CASH)}원</p></div>
                <div class="card"><h3>최종 자산</h3><p style="color:#dc2626; font-weight:700;">{fmt_man(assets[-1])}원</p></div>
                <div class="card"><h3>누적 배당금</h3><p style="color:#166534; font-weight:700;">{fmt_man(int(total_div))}원</p></div>
                <div class="card"><h3>총 수익률</h3><p style="color:#dc2626; font-weight:700;">{((assets[-1]/INITIAL_CASH)-1)*100:.2f}%</p></div>
            </div>
            
            <div class="section-title">📉 배당금 및 주당 배당금 추이</div>
            <div class="chart-container"><canvas id="divChart"></canvas></div>
            
            <div class="section-title">📈 자산 성장 추이</div>
            <div class="chart-container"><canvas id="assetChart"></canvas></div>

            <div class="section-title">📅 월별 요약</div>
            <div class="table-responsive">
                <table>
                    <thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead>
                    <tbody>{summary_rows}</tbody>
                </table>
            </div>
            
            <div class="section-title">🔍 상세 거래 내역</div>
            <p class="note-text">
                ※ 재투자는 받은 배당금을 그 다음 거래일에 매매하는 걸로 가정했습니다.<br>
                ※ 월말평가는 당월 마지막 거래일 종가와 수량을 계산한 값입니다.
            </p>
            <div class="table-responsive">
                <table class="table-detailed">
                    <thead><tr><th>날짜</th><th>구분</th><th>종목</th><th>단가</th><th>수량</th><th>거래금액</th><th>배당금</th><th>잔고</th><th>총자산</th></tr></thead>
                    <tbody>{detailed_rows}</tbody>
                </table>
            </div>
        </div>
        <script>
            document.addEventListener("DOMContentLoaded", function() {{
                new Chart(document.getElementById('divChart'), {{
                    type: 'bar',
                    data: {{
                        labels: {json.dumps(labels)},
                        datasets: [
                            {{ label: '배당금(원)', data: {json.dumps(divs)}, backgroundColor: '#10b981', yAxisID: 'y' }},
                            {{ label: '주당 배당금(원)', data: {json.dumps(dps_list)}, type: 'line', borderColor: '#f59e0b', yAxisID: 'y1', tension: 0.3 }}
                        ]
                    }},
                    options: {{ 
                        responsive: true, maintainAspectRatio: false,
                        scales: {{ y: {{ position: 'left' }}, y1: {{ position: 'right', grid: {{ drawOnChartArea: false }} }} }} 
                    }}
                }});

                new Chart(document.getElementById('assetChart'), {{
                    type: 'line',
                    data: {{
                        labels: {json.dumps(labels)},
                        datasets: [{{ label: '총자산(원)', data: {json.dumps(assets)}, borderColor: '#ef4444', fill: false, tension: 0.1 }}]
                    }},
                    options: {{ responsive: true, maintainAspectRatio: false }}
                }});
            }});
        </script>
        </body>
        </html>
        """
        
        # 파일로 저장하지 않고 화면에 바로 렌더링! (높이를 넉넉하게 2000px로 줌)
        components.html(html_template, height=2000, scrolling=True)

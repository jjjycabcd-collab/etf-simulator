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
st.title("📊 ETF 배당 시뮬레이터")

# ==========================================
# API 키 설정 (Streamlit Secrets 활용)
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

def fetch_actual_prices(code, start_year, end_year, token):
    if not code: return pd.Series(dtype=float)
    
    # 파일 캐시 확인 (가격 데이터도 저장하여 API 호출 횟수 절약)
    price_file = f"price_data_{code}.json"
    if os.path.exists(price_file):
        try:
            with open(price_file, "r") as f:
                cached = json.load(f)
            # 날짜를 Timestamp로 복구
            series = pd.Series({pd.to_datetime(k): v for k, v in cached.items()}).sort_index()
            if not series.empty and series.index[0].year <= start_year and series.index[-1].year >= end_year:
                return series
        except: pass

    # KIS API로 실제 시장가 종가 수집
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST03010100", "custtype": "P"}
    all_prices, start_dt, current_end = {}, f"{start_year}0101", f"{end_year}1231"
    
    while True:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": current_end, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"} # 0: 실제주가
        try:
            res = requests.get(url, headers=headers, params=params)
            data = res.json()
            if data['rt_cd'] != '0' or not data.get('output2'): break
            for row in data['output2']:
                if row['stck_bsop_date']:
                    all_prices[pd.to_datetime(row['stck_bsop_date'])] = int(row['stck_clpr'])
            oldest = data['output2'][-1]['stck_bsop_date']
            if oldest <= start_dt or len(data['output2']) < 100: break
            current_end = (pd.to_datetime(oldest) - pd.Timedelta(days=1)).strftime('%Y%m%d')
        except: break
    
    price_series = pd.Series(all_prices).sort_index()
    
    # 파일 저장
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
# UI 영역
# ==========================================
with st.sidebar:
    st.header("⚙️ 시뮬레이션 설정")
    cash_input = st.text_input("초기 투자금 (원)", "40000000")
    period_input = st.text_input("테스트 기간 (예: 2025~2026)", "2025~2026")
    etf_input = st.text_input("종목 코드 (쉼표 구분)", "498400, 472150")
    run_btn = st.button("시뮬레이션 실행", type="primary")

if run_btn:
    with st.spinner('실제 시장가 종가를 수집 중입니다...'):
        INITIAL_CASH = int(re.sub(r'[^0-9]', '', cash_input))
        years = [int(y) for y in period_input.split('~')]
        YEAR_RANGE = list(range(years[0], years[1] + 1))
        
        KIS_TOKEN = get_kis_token()
        codes = [c.strip() for c in etf_input.replace(',', ' ').split() if c.strip().isdigit()]
        K_CODE, T_CODE = codes[0], codes[1] if len(codes) > 1 else None
        
        K_NAME = fetch_stock_name(K_CODE, KIS_TOKEN)
        T_NAME = fetch_stock_name(T_CODE, KIS_TOKEN) if T_CODE else ""
        
        k_prices_all = fetch_actual_prices(K_CODE, YEAR_RANGE[0], YEAR_RANGE[-1], KIS_TOKEN)
        t_prices_all = fetch_actual_prices(T_CODE, YEAR_RANGE[0], YEAR_RANGE[-1], KIS_TOKEN) if T_CODE else pd.Series()
        
        k_divs_all = scrape_dividend_data(K_CODE, tuple(YEAR_RANGE))
        t_divs_all = scrape_dividend_data(T_CODE, tuple(YEAR_RANGE)) if T_CODE else {}

        # 시뮬레이션 로직
        history, cash, k_sh, t_sh, total_div, first_buy = [], INITIAL_CASH, 0, 0, 0, False
        target_ym = [(y, m) for y in YEAR_RANGE for m in range(1, 13)]

        def get_safe_price(ps, y, m, d):
            if ps.empty: return None, None
            f = ps.index[ps.index >= pd.Timestamp(y, m, d)]
            return (f[0], int(ps.loc[f[0]])) if not f.empty else (None, None)

        for y, m in target_ym:
            k_d, t_d = k_divs_all[y][m-1], t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
            
            # 1. 배당금 수령 (TIGER)
            if T_CODE and t_sh > 0 and t_d['val'] > 0:
                dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                if dt:
                    dv = t_sh * t_d['val']; cash += dv; total_div += dv
                    history.append({'날짜': dt.strftime('%y/%m/%d'), '구분': '배당', '종목': T_CODE, '단가': t_d['val'], '수량': t_sh, '수령배당금': dv, '현금잔고': cash, '총자산': cash + (t_sh*p)})
            
            # 2. TIGER 매도 및 KODEX 매수 (SWING)
            if T_CODE and t_sh > 0:
                dt, p = get_safe_price(t_prices_all, y, m, t_d['reinv_day'])
                if dt:
                    sell = t_sh * p; cash += sell
                    history.append({'날짜': dt.strftime('%y/%m/%d'), '구분': '매도', '종목': T_CODE, '단가': p, '수량': t_sh, '수령배당금': 0, '현금잔고': cash, '총자산': cash}); t_sh = 0
                    dt_k, p_k = get_safe_price(k_prices_all, dt.year, dt.month, dt.day)
                    if dt_k:
                        k_sh = cash // p_k; cash -= (k_sh*p_k)
                        history.append({'날짜': dt_k.strftime('%y/%m/%d'), '구분': '매수', '종목': K_CODE, '단가': p_k, '수량': k_sh, '수령배당금': 0, '현금잔고': cash, '총자산': cash + (k_sh*p_k)})

            # 3. KODEX 매수 (첫 달 또는 싱글 모드)
            if not first_buy:
                dt, p = get_safe_price(k_prices_all, y, m, 1)
                if dt:
                    k_sh = cash // p; cash -= (k_sh*p); first_buy = True
                    history.append({'날짜': dt.strftime('%y/%m/%d'), '구분': '매수', '종목': K_CODE, '단가': p, '수량': k_sh, '수령배당금': 0, '현금잔고': cash, '총자산': cash + (k_sh*p)})

            # 4. 배당금 수령 및 TIGER 교체 (KODEX)
            if k_sh > 0 and k_d['val'] > 0:
                dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                if dt:
                    dv = k_sh * k_d['val']; cash += dv; total_div += dv
                    history.append({'날짜': dt.strftime('%y/%m/%d'), '구분': '배당', '종목': K_CODE, '단가': k_d['val'], '수량': k_sh, '수령배당금': dv, '현금잔고': cash, '총자산': cash + (k_sh*p)})
                    if T_CODE:
                        dt_s, p_s = get_safe_price(k_prices_all, y, m, k_d['reinv_day'])
                        if dt_s:
                            sell = k_sh * p_s; cash += sell;
                            history.append({'날짜': dt_s.strftime('%y/%m/%d'), '구분': '매도', '종목': K_CODE, '단가': p_s, '수량': k_sh, '수령배당금': 0, '현금잔고': cash, '총자산': cash}); k_sh = 0
                            dt_t, p_t = get_safe_price(t_prices_all, dt_s.year, dt_s.month, dt_s.day)
                            if dt_t:
                                t_sh = cash // p_t; cash -= (t_sh*p_t)
                                history.append({'날짜': dt_t.strftime('%y/%m/%d'), '구분': '매수', '종목': T_CODE, '단가': p_t, '수량': t_sh, '수령배당금': 0, '현금잔고': cash, '총자산': cash + (t_sh*p_t)})

            # 월말 평가
            k_m = k_prices_all[k_prices_all.index.month == m]
            t_m = t_prices_all[t_prices_all.index.month == m] if T_CODE else pd.Series()
            if not k_m.empty:
                last_dt = k_m.index[-1]
                cur_asset = cash + (k_sh * int(k_prices_all.loc[last_dt])) + (t_sh * int(t_prices_all.loc[t_m.index[-1]]) if not t_m.empty else 0)
                history.append({'날짜': last_dt.strftime('%y/%m/%d'), '구분': '평가', '종목': '-', '단가': 0, '수량': 0, '수령배당금': 0, '현금잔고': cash, '총자산': cur_asset})

        # 결과 렌더링
        df = pd.DataFrame(history)
        st.write(f"### {K_NAME} {T_NAME} 결과 요약")
        c1, c2, c3 = st.columns(3)
        c1.metric("최종 자산", f"{fmt_man(history[-1]['총자산'])}원")
        c2.metric("누적 배당금", f"{fmt_man(total_div)}원")
        c3.metric("수익률", f"{((history[-1]['총자산']/INITIAL_CASH)-1)*100:.2f}%")
        st.dataframe(df, use_container_width=True)

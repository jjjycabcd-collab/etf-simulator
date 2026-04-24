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

KIS_TOKEN = None

# API 키 설정
try:
    KIS_APP_KEY = st.secrets["KIS_APP_KEY"]
    KIS_APP_SECRET = st.secrets["KIS_APP_SECRET"]
except:
    KIS_APP_KEY = "YOUR_APP_KEY_HERE"
    KIS_APP_SECRET = "YOUR_APP_SECRET_HERE"

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

def fetch_actual_prices(code, start_date, end_date, token):
    if not code: return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    
    price_file = f"price_market_v6_{code}.json"
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
        
        # [수정] 상단 타이틀 형식 원래대로 원복
        display_name = f"{K_NAME_RAW.split(' (')[0]}"
        if T_CODE: display_name += f", {T_NAME_RAW.split(' (')[0]}"
        st.title(f"📊 {period_input} {display_name} ({', '.join(codes)}) 리포트")
        
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
            k_d = k_divs_all[y][m-1]
            t_d = t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
            
            # ===============================================
            # [수정] 단일 종목 (SINGLE) 모드 로직 보완 (재투자 포함)
            # ===============================================
            if not T_CODE:
                if not first_buy:
                    dt, p = get_safe_price(k_prices_all, y, m, 1)
                    if dt:
                        k_sh = cash // p; cash -= (k_sh*p); first_buy = True
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})

                dt_pay = None
                if k_sh > 0 and k_d['val'] > 0:
                    dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                    if dt:
                        dt_pay = dt
                        dv = k_sh * k_d['val']; cash += dv; total_div += dv
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                
                # 배당금 익일 재투자
                if dt_pay:
                    found = k_prices_all.index[k_prices_all.index > dt_pay]
                    if not found.empty and found[0].year == y and found[0].month == m:
                        dt_re = found[0]
                        p_re = int(k_prices_all.loc[dt_re])
                        if cash >= p_re:
                            add_sh = cash // p_re
                            if add_sh > 0:
                                cash -= (add_sh * p_re)
                                k_sh += add_sh
                                history.append({'연도':y,'월':f"{m}월",'날짜':dt_re.strftime('%y/%m/%d'),'구분':'재투자','종목':K_CODE,'단가':p_re,'수량':add_sh,'거래금액':add_sh*p_re,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_re),'배당률':0.0})

                k_m_prices = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)]
                if not k_m_prices.empty:
                    last_dt = k_m_prices.index[-1]
                    cur_p = int(k_m_prices.iloc[-1])
                    history.append({'연도':y,'월':f"{m}월",'날짜':last_dt.strftime('%y/%m/%d'),'구분':'평가','종목':K_CODE,'단가':cur_p,'수량':k_sh,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*cur_p),'배당률':0.0})

            # ===============================================
            # 스윙 교체 (SWING) 모드 로직
            # ===============================================
            else:
                if t_sh > 0:
                    dt_pay = None
                    if t_d['val'] > 0:
                        dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                        if dt:
                            dt_pay = dt
                            dv = t_sh * t_d['val']; cash += dv; total_div += dv
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':T_CODE,'단가':t_d['val'],'수량':t_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(t_sh*p),'배당률':t_d['yield']})
                    
                    dt_switch = None
                    if dt_pay:
                        found = t_prices_all.index[t_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m:
                            dt_switch = found[0]
                    else:
                        dt_s, _ = get_safe_price(t_prices_all, y, m, t_d['reinv_day'])
                        dt_switch = dt_s

                    if dt_switch:
                        p_s = int(t_prices_all.loc[dt_switch])
                        sell_amt = t_sh * p_s; cash += sell_amt
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt_switch.strftime('%y/%m/%d'),'구분':'매도','종목':T_CODE,'단가':p_s,'수량':t_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); t_sh = 0
                        
                        if dt_switch in k_prices_all.index:
                            p_k = int(k_prices_all.loc[dt_switch])
                            k_sh = cash // p_k; cash -= (k_sh*p_k)
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt_switch.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p_k,'수량':k_sh,'거래금액':k_sh*p_k,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_k),'배당률':0.0})

                if not first_buy:
                    dt, p = get_safe_price(k_prices_all, y, m, 1)
                    if dt:
                        k_sh = cash // p; cash -= (k_sh*p); first_buy = True
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})

                if k_sh > 0:
                    dt_pay = None
                    if k_d['val'] > 0:
                        dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                        if dt:
                            dt_pay = dt
                            dv = k_sh * k_d['val']; cash += dv; total_div += dv
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                    
                    dt_switch = None
                    if dt_pay:
                        found = k_prices_all.index[k_prices_all.index > dt_pay

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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정 및 상태 초기화
# ==========================================
st.set_page_config(page_title="ETF 백테스트", layout="wide")

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
def fetch_stock_name(code):
    fallback_names = {'498400': 'KODEX 200타겟위클리커버드콜', '472150': 'TIGER 배당커버드콜액티브'}
    name = fallback_names.get(code, "종목")
    if not code: return ""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            title_wrap = soup.find('div', {'class': 'wrap_company'})
            if title_wrap: name = title_wrap.find('h2').find('a').text.strip()
    except: pass
    return f"{name}({code})"

def fetch_actual_prices(code, start_date, end_date):
    empty_series = pd.Series(dtype=float, index=pd.to_datetime([]))
    if not code: return empty_series.copy()
    price_file = f"price_market_naver_unadj_{code}.json"
    if os.path.exists(price_file):
        try:
            with open(price_file, "r") as f: cached = json.load(f)
            if cached:
                series = pd.Series({pd.to_datetime(k): v for k, v in cached.items()}).sort_index()
                if not series.empty and series.index[0] <= start_date and series.index[-1] >= end_date: return series
        except: pass
    all_prices = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': f'https://finance.naver.com/item/sise.naver?code={code}'}
    stop_flag = False
    for page in range(1, 301):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        try:
            res = requests.get(url, headers=headers, timeout=5); res.encoding = 'euc-kr' 
            soup = BeautifulSoup(res.text, 'html.parser'); rows = soup.find_all('tr')
            page_has_data = False
            for row in rows:
                tds = row.find_all('td')
                if len(tds) >= 7:
                    dt_td, pr_td = tds[0].find('span', class_='tah'), tds[1].find('span', class_='tah')
                    if dt_td and pr_td:
                        date_str, price_str = dt_td.text.strip(), pr_td.text.strip().replace(',', '')
                        if date_str and price_str.isdigit():
                            dt = pd.to_datetime(date_str.replace('.', '-'))
                            all_prices[dt] = int(price_str)
                            page_has_data = True
                            if dt < start_date - pd.Timedelta(days=10): stop_flag = True
            if not page_has_data or stop_flag: break
        except: break
    if not all_prices: return empty_series.copy()
    price_series = pd.Series(all_prices).sort_index()
    try:
        with open(price_file, "w") as f: json.dump({k.strftime('%Y-%m-%d'): v for k, v in all_prices.items()}, f)
    except: pass
    return price_series

def scrape_dividend_data(code, years_tuple):
    """에러 메시지까지 함께 반환하도록 수정된 스크래핑 함수"""
    years = list(years_tuple)
    file_path = f"dividend_data_{code}.json"
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                parsed_cache = {int(k): v for k, v in cached_data.items()}
                if all(y in parsed_cache for y in years): return parsed_cache, ""
        except: pass

    div_map = {y: [{'val':0, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)] for y in years}
    driver = None
    error_msg = ""
    
    try:
        options = webdriver.ChromeOptions()
        # 💡 클라우드 환경에서 충돌을 방지하기 위한 안전한 옵션 세팅
        options.add_argument('--headless') 
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
        
        # 💡 Streamlit Cloud의 시스템 크롬 드라이버를 최우선으로 찾습니다.
        chromedriver_path = shutil.which("chromedriver")
        if chromedriver_path:
            driver = webdriver.Chrome(service=Service(chromedriver_path), options=options)
        else:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
        driver.get(f"https://www.etfcheck.co.kr/mobile/etpitem/{code}/cash/hist")
        
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-box table tbody tr td")))
        except Exception:
            error_msg = "화면은 열렸으나 테이블 데이터가 비동기로 렌더링되지 않았습니다. (봇 차단 의심)"
            
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        rows = soup.select('div.table-box table tbody tr')
        
        if not rows and not error_msg:
            error_msg = "'tr' 태그를 전혀 찾을 수 없습니다. (HTML 구조 변경 또는 캡차 페이지)"
            
        for row in rows:
            tds = row.find_all('td')
            if len(tds) >= 2:
                try:
                    ex_date = pd.to_datetime(tds[0].text.strip())
                    div_val = int(re.sub(r'[^0-9]', '', tds[1].text.strip()))
                    div_yield_val = float(re.sub(r'[^0-9.]', '', tds[2].text.strip())) if len(tds) >= 3 else 0.0
                    pay_dt = ex_date + pd.DateOffset(months=1) if ex_date.day > 16 else ex_date
                    p_day, r_day = (2, 3) if ex_date.day > 16 else (17, 18)
                    if pay_dt.year in years: div_map[pay_dt.year][pay_dt.month-1] = {'val': div_val, 'pay_day': p_day, 'reinv_day': r_day, 'yield': div_yield_val}
                except: pass
                
    except Exception as e:
        # 💡 브라우저가 뻗어버리면 이 예외처리로 빠지게 됩니다.
        error_msg = f"크롬 브라우저 초기화 및 실행 실패: {str(e)}"
    finally:
        if driver: driver.quit()

    # Fallback 로직 (데이터를 못 가져왔을 때 기본값)
    for y in years:
        if not any(item['val'] > 0 for item in div_map[y]):
            if code == '498400': div_map[y] = [{'val':230, 'pay_day':17, 'reinv_day':18, 'yield':0.0} for _ in range(12)]
            elif code == '472150': div_map[y] = [{'val':250, 'pay_day':2, 'reinv_day':3, 'yield':0.0} for _ in range(12)]
            
    try:
        with open(file_path, "w", encoding="utf-8") as f: json.dump(div_map, f, ensure_ascii=False, indent=4)
    except: pass
    
    # 💡 데이터와 함께 발생한 에러 메시지를 같이 반환합니다.
    return div_map, error_msg

def fmt_man(val): return "0" if val == 0 else (f"{int(val) // 10000:,}만" if abs(val) >= 10000 else f"{int(val):,}")

# ==========================================
# UI 영역
# ==========================================
st.title("📊 ETF 백테스트")

if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 시뮬레이션 설정 다시 하기", use_container_width=True):
        st.session_state.show_settings = True; st.rerun()

if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 시뮬레이션 설정")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 투자금 (원)", "40000000")
            period_input = st.text_input("백테스트 기간 (2025 또는 2025.1~2026.1)", "2025~2026")
        with col2:
            etf_input = st.text_input("종목 코드 (쉼표 구분)", "498400, 472150")
            div_option = st.radio("배당금 처리", ["재투자", "인출(생활비)"], index=0, horizontal=True)
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('데이터 분석 및 백테스트 중...'):
            now = datetime.datetime.now(); curr_year, curr_month = now.year, now.month
            INITIAL_CASH = int(re.sub(r'[^0-9]', '', cash_input)) if cash_input else 0
            
            def parse_date_str(s, is_end=False):
                if '.' in s: parts = s.split('.'); return int(parts[0]), int(parts[1])
                return int(s), (12 if is_end else 1)
            
            try:
                if '~' in period_input:
                    s_part, e_part = period_input.split('~')
                    start_year, start_month = parse_date_str(s_part.strip())
                    end_year, end_month = parse_date_str(e_part.strip(), True)
                else:
                    start_year, start_month = parse_date_str(period_input.strip()); end_year, end_month = parse_date_str(period_input.strip(), True)
            except: start_year, start_month, end_year, end_month = 2025, 1, curr_year, curr_month

            if end_year > curr_year or (end_year == curr_year and end_month > curr_month): end_year, end_month = curr_year, curr_month
            start_ts, end_ts = pd.Timestamp(start_year, start_month, 1), pd.Timestamp(end_year, end_month, 28)
            YEAR_RANGE = list(range(start_year, end_year + 1))
            target_ym = [(y, m) for y in YEAR_RANGE for m in range(1, 13) if not (y == start_year and m < start_month) and not (y == end_year and m > end_month)]

            codes = [c.strip() for c in etf_input.replace(',', ' ').split() if c.strip().isdigit()]
            K_CODE = codes[0] if codes else ""; T_CODE = codes[1] if len(codes) > 1 else None
            K_NAME_RAW, T_NAME_RAW = fetch_stock_name(K_CODE), fetch_stock_name(T_CODE) if T_CODE else ""
            
            st.session_state.display_title = f"### 📈 {period_input} {K_NAME_RAW}" + (f", {T_NAME_RAW}" if T_CODE else "")
            
            k_prices_all = fetch_actual_prices(K_CODE, start_ts, end_ts)
            t_prices_all = fetch_actual_prices(T_CODE, start_ts, end_ts) if T_CODE else pd.Series(dtype=float, index=pd.to_datetime([]))
            
            # 💡 수정된 함수 호출부: 에러 메시지를 함께 받아와서 화면에 뿌려줍니다.
            k_divs_all, k_err = scrape_dividend_data(K_CODE, tuple(YEAR_RANGE))
            if T_CODE:
                t_divs_all, t_err = scrape_dividend_data(T_CODE, tuple(YEAR_RANGE))
            else:
                t_divs_all, t_err = {}, ""

            if k_err: st.error(f"🚨 [{K_CODE}] 수집 실패: {k_err}")
            if t_err: st.error(f"🚨 [{T_CODE}] 수집 실패: {t_err}")

            history, cash, k_sh, t_sh, total_div, first_buy = [], INITIAL_CASH, 0, 0, 0, False

            def get_safe_price(ps, y, m, d, after=False):
                if ps.empty: return None, None
                target_dt = pd.Timestamp(y, m, d); found = ps.index[ps.index > target_dt] if after else ps.index[ps.index >= target_dt]
                return (found[0], int(ps.loc[found[0]])) if not found.empty and found[0].year == y and found[0].month == m else (None, None)

            for y, m in target_ym:
                k_d = k_divs_all.get(y, [None]*12)[m-1] if K_CODE else None
                t_d = t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
                
                if not T_CODE and K_CODE:
                    if not first_buy:
                        dt, p = get_safe_price(k_prices_all, y, m, 1)
                        if dt: k_sh = cash // p; cash -= (k_sh * p); first_buy = True; history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})
                    
                    dt_pay = None
                    if k_sh > 0 and k_d and k_d['val'] > 0:
                        dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                        if dt: dt_pay = dt; dv = k_sh * k_d['val']; total_div += dv; cash += dv if div_option == "재투자" else 0; history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                    
                    if div_option == "재투자" and dt_pay:
                        found = k_prices_all.index[k_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m:
                            dt_re, p_re = found[0], int(k_prices_all.loc[found[0]])
                            if cash >= p_re: add_sh = cash // p_re; cash -= (add_sh * p_re); k_sh += add_sh; history.append({'연도':y,'월':f"{m}월",'날짜':dt_re.strftime('%y/%m/%d'),'구분':'재투자','종목':K_CODE,'단가':p_re,'수량':add_sh,'거래금액':add_sh*p_re,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_re),'배당률':0.0})
                    
                    km = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)]
                    if not km.empty: history.append({'연도':y,'월':f"{m}월",'날짜':km.index[-1].strftime('%y/%m/%d'),'구분':'평가','종목':K_CODE,'단가':int(km.iloc[-1]),'수량':k_sh,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*int(km.iloc[-1])),'배당률':0.0})
                
                elif T_CODE and K_CODE:
                    if t_sh > 0:
                        dt_pay = None
                        if t_d and t_d['val'] > 0:
                            dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                            if dt: dt_pay = dt; dv = t_sh * t_d['val']; total_div += dv; cash += dv if div_option == "재투자" else 0; history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':T_CODE,'단가':t_d['val'],'수량':t_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(t_sh*p),'배당률':t_d['yield']})
                        
                        dt_sw = t_prices_all.index[t_prices_all.index > dt_pay][0] if dt_pay and not t_prices_all.index[t_prices_all.index > dt_pay].empty and t_prices_all.index[t_prices_all.index > dt_pay][0].year == y and t_prices_all.index[t_prices_all.index > dt_pay][0].month == m else get_safe_price(t_prices_all, y, m, t_d['reinv_day'] if t_d else 18)[0]
                        if dt_sw:
                            p_s = int(t_prices_all.loc[dt_sw]); sell_amt = t_sh * p_s; cash += sell_amt; history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매도','종목':T_CODE,'단가':p_s,'수량':t_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); t_sh = 0
                            if dt_sw in k_prices_all.index: p_k = int(k_prices_all.loc[dt_sw]); k_sh = cash // p_k; cash -= (k_sh * p_k); history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p_k,'수량':k_sh,'거래금액':k_sh*p_k,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_k),'배당률':0.0})
                    
                    if not first_buy:
                        dt, p = get_safe_price(k_prices_all, y, m, 1)
                        if dt: k_sh = cash // p; cash -= (k_sh * p); first_buy = True; history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})
                    
                    if k_sh > 0:
                        dt_pay = None
                        if k_d and k_d['val'] > 0:
                            dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                            if dt: dt_pay = dt; dv = k_sh * k_d['val']; total_div += dv; cash += dv if div_option == "재투자" else 0; history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                        
                        dt_sw = k_prices_all.index[k_prices_all.index > dt_pay][0] if dt_pay and not k_prices_all.index[k_prices_all.index > dt_pay].empty and k_prices_all.index[k_prices_all.index > dt_pay][0].year == y and k_prices_all.index[k_prices_all.index > dt_pay][0].month == m else get_safe_price(k_prices_all, y, m, k_d['reinv_day'] if k_d else 18)[0]
                        if dt_sw:
                            p_s = int(k_prices_all.loc[dt_sw]); sell_amt = k_sh * p_s; cash += sell_amt; history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매도','종목':K_CODE,'단가':p_s,'수량':k_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); k_sh = 0
                            if dt_sw in t_prices_all.index: p_t = int(t_prices_all.loc[dt_sw]); t_sh = cash // p_t; cash -= (t_sh * p_t); history.append({'연도':y,'월':f"{m}월",'날짜':dt_sw.strftime('%y/%m/%d'),'구분':'매수','종목':T_CODE,'단가':p_t,'수량':t_sh,'거래금액':t_sh*p_t,'수령배당금':0,'현금잔고':cash,'총자산':cash+(t_sh*p_t),'배당률':0.0})
                    
                    km, tm = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)], t_prices_all[(t_prices_all.index.year == y) & (t_prices_all.index.month == m)]
                    if not km.empty or not tm.empty:
                        cur_t = K_CODE if k_sh > 0 else (T_CODE if t_sh > 0 else "-"); cur_s = k_sh if k_sh > 0 else t_sh
                        cur_p = int(km.iloc[-1]) if k_sh > 0 and not km.empty else (int(tm.iloc[-1]) if t_sh > 0 and not tm.empty else 0)
                        last_dt = km.index[-1].strftime('%y/%m/%d') if not km.empty else tm.index[-1].strftime('%y/%m/%d')
                        val_total = (k_sh*int(km.iloc[-1]) if not km.empty else 0) + (t_sh*int(tm.iloc[-1]) if not tm.empty else 0)
                        history.append({'연도':y,'월':f"{m}월",'날짜':last_dt,'구분':'평가','종목':cur_t,'단가':cur_p,'수량':cur_s,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+val_total,'배당률':0.0})

            df_hist = pd.DataFrame(history)
            monthly_summary, labels, divs, dps_list, assets, prev_asset = [], [], [], [], [], INITIAL_CASH
            
            for y, m in target_ym:
                m_data = df_hist[(df_hist['연도'] == y) & (df_hist['월'] == f"{m}월")]
                if m_data.empty: continue
                m_div = int(m_data['수령배당금'].sum()); m_final = int(m_data.iloc[-1]['총자산']); m_dps = int(m_data[m_data['구분'] == '배당']['단가'].sum()); m_yld = float(m_data[m_data['구분'] == '배당']['배당률'].sum())
                labels.append(f"{y}.{m}"); divs.append(m_div); dps_list.append(m_dps); assets.append(m_final)
                monthly_summary.append({'기간': f"{y}.{m:02d}", '주당배당금': m_dps, '배당률': m_yld, '배당금': m_div, '총자산': m_final, '증감': int(m_final - prev_asset)}); prev_asset = m_final

            df_sum = pd.DataFrame(monthly_summary)
            json_summary_str = df_sum.to_json(orient='records', force_ascii=False) if not df_sum.empty else "[]"
            json_history_str = df_hist.to_json(orient='records', force_ascii=False) if not df_hist.empty else "[]"

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH, 'last_asset': assets[-1] if assets else INITIAL_CASH, 'total_div': total_div,
                'json_summary': json_summary_str, 'json_history': json_history_str,
                'labels': labels, 'divs': divs, 'dps_list': dps_list, 'assets': assets, 'div_option': div_option
            }
            st.session_state.run_clicked = True; st.session_state.show_settings = False; st.rerun()

# ==========================================
# 결과 출력 영역 (Client-Side Rendering)
# ==========================================
if st.session_state.run_clicked and st.session_state.sim_result_data:
    res = st.session_state.sim_result_data
    st.markdown(st.session_state.display_title)
    
    total_prof = (res['last_asset'] if res['div_option'] == "재투자" else res['last_asset'] + res['total_div']) - res['initial_cash']
    prof_rate = (total_prof / res['initial_cash']) * 100 if res['initial_cash'] else 0
    prof_col = "#dc2626" if total_prof > 0 else "#2563eb"

    json_summary, json_history = res.get('json_summary', "[]"), res.get('json_history', "[]")

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
        <div class="card"><h3>초기 투자금</h3><p style="color:#3b82f6;">{fmt_man(res['initial_cash'])}원</p></div>
        <div class="card"><h3>최종 자산</h3><p style="color:#dc2626;">{fmt_man(res['last_asset'])}원</p></div>
        <div class="card"><h3>누적 배당금</h3><p style="color:#166534;">{fmt_man(int(res['total_div']))}원</p></div>
        <div class="card"><h3>총 수익금</h3><p style="color:{prof_col};">{fmt_man(total_prof)}원</p></div>
        <div class="card"><h3>총 수익률</h3><p style="color:#dc2626;">{prof_rate:.2f}%</p></div>
    </div>
    
    <div class="section-title">📉 배당금 및 주당 배당금 추이</div>
    <div class="chart-container"><canvas id="divChart"></canvas></div>
    
    <div class="section-title">📈 자산 성장 추이</div>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    
    <div class="header-flex"><h3 class="header-title">📅 월별 요약</h3><select class="sort-select" onchange="renderSummary(this.value)"><option value="desc" selected>최신순</option><option value="asc">과거순</option></select></div>
    <div class="table-wrapper"><table><thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead><tbody id="summary-tbody"></tbody></table></div>

    <div class="header-flex"><h3 class="header-title">🔍 상세 거래 내역</h3><select class="sort-select" onchange="renderHistory(this.value)"><option value="asc" selected>과거순</option><option value="desc">최신순</option></select></div>
    <div class="table-wrapper"><table><thead><tr><th>날짜</th><th>구분</th><th>종목</th><th>단가</th><th>수량</th><th>거래금액</th><th>배당금</th><th>잔고</th><th>총자산</th></tr></thead><tbody id="history-tbody"></tbody></table></div>

    <script>
        const summaryData = {json_summary}; const historyData = {json_history};
        function fmtMan(val) {{ if (val === 0 || val === '0') return "0"; let num = parseInt(val, 10); return Math.abs(num) >= 10000 ? Math.floor(num / 10000).toLocaleString() + "만" : num.toLocaleString(); }}
        function getCls(cat) {{ return (cat.includes("매수") || cat.includes("재투자")) ? "buy" : (cat.includes("매도") ? "sell" : (cat.includes("배당") ? "div" : "eval")); }}
        function renderSummary(order) {{
            let data = [...summaryData]; if (order === 'desc') data.reverse(); 
            document.getElementById('summary-tbody').innerHTML = data.map(s => `<tr><td>${{s.기간}}</td><td>${{Math.floor(s.주당배당금).toLocaleString()}}</td><td style='color:#f59e0b; font-weight:600;'>${{parseFloat(s.배당률).toFixed(2)}}%</td><td>${{fmtMan(s.배당금)}}</td><td><b>${{fmtMan(s.총자산)}}</b></td><td style='color:${{s.증감 > 0 ? '#dc2626' : '#2563eb'}}; font-weight:600;'>${{fmtMan(s.증감)}}</td></tr>`).join('');
        }}
        function renderHistory(order) {{
            let data = [...historyData]; if (order === 'desc') data.reverse();
            document.getElementById('history-tbody').innerHTML = data.map(r => `<tr${{getCls(r.구분) === 'div' ? " class='row-div'" : ""}}><td>${{r.날짜}}</td><td><span class='badge ${{getCls(r.구분)}}'>${{r.구분}}</span></td><td style='text-align:center;'>${{r.종목}}</td><td>${{Math.floor(r.단가).toLocaleString()}}</td><td>${{Math.floor(r.수량).toLocaleString()}}</td><td>${{r.거래금액 > 0 ? fmtMan(r.거래금액) : '-'}}</td><td class='div-val'>${{r.수령배당금 > 0 ? '+' + fmtMan(r.수령배당금) : '-'}}</td><td>${{fmtMan(r.현금잔고)}}</td><td style='font-weight:700;'>${{fmtMan(r.총자산)}}</td></tr>`).join('');
        }}
        renderSummary('desc'); renderHistory('asc');
        new Chart(document.getElementById('divChart'), {{ type: 'bar', data: {{ labels: {json.dumps(res['labels'])}, datasets: [{{ label: '배당금(원)', data: {json.dumps(res['divs'])}, backgroundColor: '#10b981', yAxisID: 'y' }}, {{ label: '주당 배당금(원)', data: {json.dumps(res['dps_list'])}, type: 'line', borderColor: '#f59e0b', yAxisID: 'y1', tension: 0.3 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
        new Chart(document.getElementById('assetChart'), {{ type: 'line', data: {{ labels: {json.dumps(res['labels'])}, datasets: [{{ label: '총자산(원)', data: {json.dumps(res['assets'])}, borderColor: '#ef4444', fill: false, tension: 0.1 }}] }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
    </script>
    </body></html>
    """
    components.html(html_code, height=2200, scrolling=True)

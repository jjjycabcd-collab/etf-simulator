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
# 함수 정의부
# ==========================================

@st.cache_data(ttl=86400)
def fetch_stock_name(code):
    """네이버 증권에서 종목명 스크래핑"""
    fallback_names = {
        '498400': 'KODEX 200타겟위클리커버드콜', 
        '472150': 'TIGER 배당커버드콜액티브'
    }
    name = fallback_names.get(code, "종목")
    if not code: return ""
    
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            title_wrap = soup.find('div', {'class': 'wrap_company'})
            if title_wrap:
                name = title_wrap.find('h2').find('a').text.strip()
    except Exception as e:
        pass
        
    return f"{name} ({code})"

def fetch_actual_prices(code, start_date, end_date):
    """네이버 증권 일별 시세표에서 실제 종가(수정주가 미적용) 수집"""
    empty_series = pd.Series(dtype=float, index=pd.to_datetime([]))
    if not code: return empty_series.copy()
    
    price_file = f"price_market_naver_unadj_{code}.json"
    if os.path.exists(price_file):
        try:
            with open(price_file, "r") as f:
                cached = json.load(f)
            if cached:
                series = pd.Series({pd.to_datetime(k): v for k, v in cached.items()}).sort_index()
                if not series.empty and series.index[0] <= start_date and series.index[-1] >= end_date: 
                    return series
        except: pass

    all_prices = {}
    # 네이버 차단 방지용 상세 헤더
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': f'https://finance.naver.com/item/sise.naver?code={code}',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    stop_flag = False
    
    for page in range(1, 301):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        try:
            res = requests.get(url, headers=headers, timeout=5)
            res.encoding = 'euc-kr' 
            
            # pandas read_html 오류 방지를 위해 BeautifulSoup으로 직접 파싱
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.find_all('tr')
            page_has_data = False
            
            for row in rows:
                tds = row.find_all('td')
                if len(tds) >= 7:
                    date_td = tds[0].find('span', class_='tah')
                    price_td = tds[1].find('span', class_='tah')
                    
                    if date_td and price_td:
                        date_str = date_td.text.strip()
                        price_str = price_td.text.strip().replace(',', '')
                        
                        if date_str and price_str.isdigit():
                            dt = pd.to_datetime(date_str.replace('.', '-'))
                            all_prices[dt] = int(price_str)
                            page_has_data = True
                            
                            if dt < start_date - pd.Timedelta(days=10):
                                stop_flag = True
                                
            if not page_has_data or stop_flag:
                break
                
        except Exception as e:
            break
            
    if not all_prices:
        return empty_series.copy()
        
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
    period_input = st.text_input("백테스트 기간 (예: 2025 또는 2025.1~2026.2)", "2025.1~2026.4")
    etf_input = st.text_input("종목 코드 (쉼표 구분)", "498400, 472150")
    div_option = st.radio("배당금 처리", ["재투자", "인출(생활비)"], index=0)
    run_btn = st.button("시뮬레이션 실행", type="primary")

if not run_btn:
    st.title("📊 ETF 배당 시뮬레이터")

# ==========================================
# 실행 영역
# ==========================================
if run_btn:
    with st.spinner('네이버 증권 데이터를 스크래핑 중입니다. 잠시만 기다려주세요...'):
        now = datetime.datetime.now()
        curr_year, curr_month = now.year, now.month
        INITIAL_CASH = int(re.sub(r'[^0-9]', '', cash_input)) if cash_input else 0
        
        def parse_date_str(s, is_end=False):
            if '.' in s:
                parts = s.split('.')
                return int(parts[0]), int(parts[1])
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

        codes = [c.strip() for c in etf_input.replace(',', ' ').split() if c.strip().isdigit()]
        K_CODE = codes[0] if codes else ""
        T_CODE = codes[1] if len(codes) > 1 else None
        
        K_NAME_RAW = fetch_stock_name(K_CODE) if K_CODE else "알수없음"
        T_NAME_RAW = fetch_stock_name(T_CODE) if T_CODE else ""
        
        display_name = f"{K_NAME_RAW.split(' (')[0]}"
        if T_CODE: display_name += f", {T_NAME_RAW.split(' (')[0]}"
        st.title(f"📊 {period_input} {display_name} ({', '.join(codes)}) 백테스트")
        
        # [핵심] fallback으로 들어가는 빈 데이터에도 안전한 날짜 인덱스를 명시적으로 부여
        k_prices_all = fetch_actual_prices(K_CODE, start_ts, end_ts) if K_CODE else pd.Series(dtype=float, index=pd.to_datetime([]))
        t_prices_all = fetch_actual_prices(T_CODE, start_ts, end_ts) if T_CODE else pd.Series(dtype=float, index=pd.to_datetime([]))
        
        # 데이터 수집 실패 알림
        if K_CODE and k_prices_all.empty:
            st.error(f"⚠️ 네이버 금융에서 '{K_CODE}'의 가격 데이터를 가져오지 못했습니다. 일시적 차단이거나 종목 코드가 잘못되었습니다.")
            
        k_divs_all = scrape_dividend_data(K_CODE, tuple(YEAR_RANGE)) if K_CODE else {}
        t_divs_all = scrape_dividend_data(T_CODE, tuple(YEAR_RANGE)) if T_CODE else {}

        history, cash, k_sh, t_sh, total_div, first_buy = [], INITIAL_CASH, 0, 0, 0, False

        def get_safe_price(ps, y, m, d, after=False):
            if ps.empty: return None, None
            target_dt = pd.Timestamp(y, m, d)
            if after:
                found = ps.index[ps.index > target_dt]
            else:
                found = ps.index[ps.index >= target_dt]
            if not found.empty and found[0].year == y and found[0].month == m:
                return (found[0], int(ps.loc[found[0]]))
            return (None, None)

        for y, m in target_ym:
            k_d = k_divs_all.get(y, [None]*12)[m-1] if K_CODE else None
            t_d = t_divs_all.get(y, [None]*12)[m-1] if T_CODE else None
            
            # ==================================================
            # 단일 종목 (SINGLE) 모드 로직
            # ==================================================
            if not T_CODE and K_CODE:
                if not first_buy:
                    dt, p = get_safe_price(k_prices_all, y, m, 1)
                    if dt:
                        k_sh = cash // p; cash -= (k_sh*p); first_buy = True
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'매수','종목':K_CODE,'단가':p,'수량':k_sh,'거래금액':k_sh*p,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':0.0})

                dt_pay = None
                if k_sh > 0 and k_d and k_d['val'] > 0:
                    dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                    if dt:
                        dt_pay = dt
                        dv = k_sh * k_d['val']; total_div += dv
                        if div_option == "재투자": cash += dv
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                
                if div_option == "재투자" and dt_pay:
                    if not k_prices_all.empty:
                        found = k_prices_all.index[k_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m:
                            dt_re = found[0]
                            p_re = int(k_prices_all.loc[dt_re])
                            if cash >= p_re:
                                add_sh = cash // p_re
                                if add_sh > 0:
                                    cash -= (add_sh * p_re); k_sh += add_sh
                                    history.append({'연도':y,'월':f"{m}월",'날짜':dt_re.strftime('%y/%m/%d'),'구분':'재투자','종목':K_CODE,'단가':p_re,'수량':add_sh,'거래금액':add_sh*p_re,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*p_re),'배당률':0.0})

                # [핵심] 빈 데이터 객체에서 .year 접근시 AttributeError 원천 차단
                k_m_prices = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)] if not k_prices_all.empty else k_prices_all
                if not k_m_prices.empty:
                    last_dt = k_m_prices.index[-1]
                    cur_p = int(k_m_prices.iloc[-1])
                    history.append({'연도':y,'월':f"{m}월",'날짜':last_dt.strftime('%y/%m/%d'),'구분':'평가','종목':K_CODE,'단가':cur_p,'수량':k_sh,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+(k_sh*cur_p),'배당률':0.0})

            # ==================================================
            # 스윙 교체 (SWING) 모드 로직
            # ==================================================
            elif T_CODE and K_CODE:
                if t_sh > 0:
                    dt_pay = None
                    if t_d and t_d['val'] > 0:
                        dt, p = get_safe_price(t_prices_all, y, m, t_d['pay_day'])
                        if dt:
                            dt_pay = dt
                            dv = t_sh * t_d['val']; total_div += dv
                            if div_option == "재투자": cash += dv
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':T_CODE,'단가':t_d['val'],'수량':t_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(t_sh*p),'배당률':t_d['yield']})
                    
                    dt_switch = None
                    if dt_pay and not t_prices_all.empty:
                        found = t_prices_all.index[t_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m: dt_switch = found[0]
                    else:
                        dt_s, _ = get_safe_price(t_prices_all, y, m, t_d['reinv_day'] if t_d else 18)
                        dt_switch = dt_s

                    if dt_switch and not t_prices_all.empty:
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
                    if k_d and k_d['val'] > 0:
                        dt, p = get_safe_price(k_prices_all, y, m, k_d['pay_day'])
                        if dt:
                            dt_pay = dt
                            dv = k_sh * k_d['val']; total_div += dv
                            if div_option == "재투자": cash += dv
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt.strftime('%y/%m/%d'),'구분':'배당','종목':K_CODE,'단가':k_d['val'],'수량':k_sh,'거래금액':0,'수령배당금':dv,'현금잔고':cash,'총자산':cash+(k_sh*p),'배당률':k_d['yield']})
                    
                    dt_switch = None
                    if dt_pay and not k_prices_all.empty:
                        found = k_prices_all.index[k_prices_all.index > dt_pay]
                        if not found.empty and found[0].year == y and found[0].month == m: dt_switch = found[0]
                    else:
                        dt_s, _ = get_safe_price(k_prices_all, y, m, k_d['reinv_day'] if k_d else 18)
                        dt_switch = dt_s

                    if dt_switch and not k_prices_all.empty:
                        p_s = int(k_prices_all.loc[dt_switch])
                        sell_amt = k_sh * p_s; cash += sell_amt
                        history.append({'연도':y,'월':f"{m}월",'날짜':dt_switch.strftime('%y/%m/%d'),'구분':'매도','종목':K_CODE,'단가':p_s,'수량':k_sh,'거래금액':sell_amt,'수령배당금':0,'현금잔고':cash,'총자산':cash,'배당률':0.0}); k_sh = 0
                        
                        if dt_switch in t_prices_all.index:
                            p_t = int(t_prices_all.loc[dt_switch])
                            t_sh = cash // p_t; cash -= (t_sh*p_t)
                            history.append({'연도':y,'월':f"{m}월",'날짜':dt_switch.strftime('%y/%m/%d'),'구분':'매수','종목':T_CODE,'단가':p_t,'수량':t_sh,'거래금액':t_sh*p_t,'수령배당금':0,'현금잔고':cash,'총자산':cash+(t_sh*p_t),'배당률':0.0})

                # [핵심] 빈 데이터 객체에서 .year 접근시 AttributeError 원천 차단
                k_m_prices = k_prices_all[(k_prices_all.index.year == y) & (k_prices_all.index.month == m)] if not k_prices_all.empty else k_prices_all
                t_m_prices = t_prices_all[(t_prices_all.index.year == y) & (t_prices_all.index.month == m)] if not t_prices_all.empty else t_prices_all
                
                if not k_m_prices.empty or not t_m_prices.empty:
                    cur_ticker = K_CODE if k_sh > 0 else (T_CODE if t_sh > 0 else "-")
                    cur_sh = k_sh if k_sh > 0 else t_sh
                    cur_p = int(k_m_prices.iloc[-1]) if k_sh > 0 and not k_m_prices.empty else (int(t_m_prices.iloc[-1]) if t_sh > 0 and not t_m_prices.empty else 0)
                    last_dt = k_m_prices.index[-1] if not k_m_prices.empty else t_m_prices.index[-1]
                    val_k = k_sh * (int(k_m_prices.iloc[-1]) if not k_m_prices.empty else 0)
                    val_t = t_sh * (int(t_m_prices.iloc[-1]) if not t_m_prices.empty else 0)
                    history.append({'연도':y,'월':f"{m}월",'날짜':last_dt.strftime('%y/%m/%d'),'구분':'평가','종목':cur_ticker,'단가':cur_p,'수량':cur_sh,'거래금액':0,'수령배당금':0,'현금잔고':cash,'총자산':cash+val_k+val_t,'배당률':0.0})

        df_hist = pd.DataFrame(history)
        
        if df_hist.empty:
            df_hist = pd.DataFrame(columns=['연도', '월', '날짜', '구분', '종목', '단가', '수량', '거래금액', '수령배당금', '현금잔고', '총자산', '배당률'])

        monthly_summary, labels, divs, dps_list, assets, prev_asset = [], [], [], [], [], INITIAL_CASH
        for y, m in target_ym:
            m_data = df_hist[(df_hist['연도'] == y) & (df_hist['월'] == f"{m}월")]
            if m_data.empty: continue
            m_div = m_data['수령배당금'].sum(); m_final = m_data.iloc[-1]['총자산']
            m_dps = m_data[m_data['구분'] == '배당']['단가'].sum()
            m_yield = m_data[m_data['구분'] == '배당']['배당률'].sum()
            labels.append(f"{y}.{m}"); divs.append(int(m_div)); dps_list.append(int(m_dps)); assets.append(int(m_final))
            monthly_summary.append({'기간': f"{y}.{m:02d}", '주당배당금': m_dps, '배당률': m_yield, '배당금': m_div, '총자산': m_final, '증감': m_final - prev_asset})
            prev_asset = m_final

        last_asset = assets[-1] if assets else INITIAL_CASH

        if div_option == "재투자":
            total_profit = last_asset - INITIAL_CASH
            profit_rate = (total_profit / INITIAL_CASH) * 100 if INITIAL_CASH else 0
        else:
            total_profit = (last_asset + total_div) - INITIAL_CASH
            profit_rate = (total_profit / INITIAL_CASH) * 100 if INITIAL_CASH else 0
            
        profit_color = "#dc2626" if total_profit > 0 else "#2563eb"

        summary_rows = "".join([f"<tr><td>{s['기간']}</td><td>{int(s['주당배당금']):,}</td><td style='color:#f59e0b; font-weight:600;'>{s['배당률']:.2f}%</td><td>{fmt_man(s['배당금'])}</td><td><b>{fmt_man(s['총자산'])}</b></td><td style='color:{'#dc2626' if s['증감']>0 else '#2563eb'}; font-weight:600;'>{fmt_man(s['증감'])}</td></tr>" for s in monthly_summary[::-1]])
        def get_cls(cat): return "buy" if "매수" in cat or "재투자" in cat else "sell" if "매도" in cat else "div" if "배당" in cat else "eval"
        
        df_display = df_hist.sort_values(by=['날짜'], ascending=True) if not df_hist.empty else df_hist
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
            </style>
        </head>
        <body>
            <div class="card-grid">
                <div class="card"><h3>초기 투자금</h3><p style="color:#3b82f6; font-weight:700;">{fmt_man(INITIAL_CASH)}원</p></div>
                <div class="card"><h3>최종 자산</h3><p style="color:#dc2626; font-weight:700;">{fmt_man(last_asset)}원</p></div>
                <div class="card"><h3>누적 배당금</h3><p style="color:#166534; font-weight:700;">{fmt_man(int(total_div))}원</p></div>
                <div class="card"><h3>총 수익금</h3><p style="color:{profit_color}; font-weight:700;">{fmt_man(total_profit)}원</p></div>
                <div class="card"><h3>총 수익률</h3><p style="color:#dc2626; font-weight:700;">{profit_rate:.2f}%</p></div>
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
                ※ 재투자 옵션 선택 시 받은 배당금을 그 다음 거래일에 매매하는 걸로 가정했습니다.<br>
                ※ 인출(생활비) 옵션 선택 시 배당금은 재투자되지 않으며, '총 수익금'과 '총 수익률'은 인출한 배당금을 포함하여 계산됩니다.<br>
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

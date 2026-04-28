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

if 'show_settings' not in st.session_state:
    st.session_state.show_settings = True
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False
if 'sim_result_data' not in st.session_state:
    st.session_state.sim_result_data = None
if 'do_run' not in st.session_state:
    st.session_state.do_run = False

# --- 입력값 유지를 위한 세션 상태 초기화 ---
if 'last_inputs' not in st.session_state:
    st.session_state.last_inputs = {
        'cash': "5,000,000",
        'period': "2025.1~2026.4",
        'div': "재투자",
        'etf': "498400, 472150, 498400 + 472150",
        'strat': ["거치식 (일괄 매수)"],
        'strat_wm': ["일괄 매수", "분할 매수 (4분할)", "분할 매수 (6분할)"]
    }

if 'saved_cash' not in st.session_state: st.session_state.saved_cash = st.session_state.last_inputs['cash']
if 'saved_period' not in st.session_state: st.session_state.saved_period = st.session_state.last_inputs['period']
if 'saved_div_action' not in st.session_state: st.session_state.saved_div_action = st.session_state.last_inputs['div']
if 'saved_etf' not in st.session_state: st.session_state.saved_etf = st.session_state.last_inputs['etf']
if 'saved_strategy' not in st.session_state: st.session_state.saved_strategy = st.session_state.last_inputs['strat']
if 'saved_strategy_wm' not in st.session_state: st.session_state.saved_strategy_wm = st.session_state.last_inputs['strat_wm']

# ==========================================
# 종목 데이터 마스터 적재 (캐싱)
# ==========================================
@st.cache_data(ttl=86400)
def load_all_tickers():
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
    except:
        tickers.update({"005930": "삼성전자", "000660": "SK하이닉스"})

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json().get('result', {}).get('etfItemList', []):
                tickers[item['itemcode']] = item['itemname']
    except:
        pass
    return tickers

ALL_TICKERS = load_all_tickers()

# ==========================================
# 함수 정의부 및 콜백
# ==========================================
@st.cache_data(ttl=86400)
def get_stock_info(code):
    if not code: return ""
    if code in ALL_TICKERS: return f"{ALL_TICKERS[code]}({code})"
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
    try:
        ticker_code = f"{code}.KS" if code.isdigit() else code
        ticker = yf.Ticker(ticker_code)
        df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty and code.isdigit():
            ticker_code = f"{code}.KQ"
            ticker = yf.Ticker(ticker_code)
            df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
        if df.empty: return pd.Series(dtype=float), pd.Series(dtype=float)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close'].dropna(), df['Dividends'].replace(0, pd.NA).dropna()
    except:
        return pd.Series(dtype=float), pd.Series(dtype=float)

def append_to_etf_input(code):
    current_str = st.session_state.saved_etf
    items = [i.strip() for i in current_str.split(',') if i.strip()]
    if len(items) >= 4:
        st.toast("⚠️ 최대 4종목까지만 추가할 수 있습니다.", icon="⚠️")
    else:
        st.session_state.saved_etf = current_str + f", {code}" if current_str.strip() else code

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
    * **순수 종가 사용:** 본 시뮬레이터는 배당 수익 이중 계산 방지를 위해 수정주가(Adj Close)가 아닌 **실제 거래된 일별 종가(Close)**를 기준으로 단가를 계산합니다.
    * **배당 기준 시점:** 배당락일(Ex-Dividend Date)에 권리를 획득하며, **실제 현금 입금 및 재투자는 4일 뒤(영업일 기준 보정)**에 이루어집니다.
    * **배당풍차 모드 (A + B):** 입력창에 `498400 + 472150`과 같이 `+`로 연결하여 입력하면 **배당풍차 모드**가 작동합니다. A종목 보유 중 배당락일이 도래하면, 당일 종가에 A종목을 전량 매도하고 즉시 B종목으로 교차 매수합니다.
    """)

    with st.expander("🔍 종목 코드를 모르시나요? (국내/해외 종목 검색 및 추가)", expanded=False):
        st.markdown("👇 **찾고 싶은 종목명이나 티커(예: QQQ, 삼성전자)를 입력하고 엔터를 치세요.**")
        search_kw = st.text_input("종목 검색어 입력 (입력 후 엔터)", key="search_input_kw")
        
        if search_kw:
            with st.spinner("검색 중..."):
                search_kw_clean = search_kw.replace(" ", "").lower()
                matches = []
                for code, name in ALL_TICKERS.items():
                    if search_kw_clean in name.replace(" ", "").lower() or search_kw_clean == code.lower():
                        matches.append((code, name))
                try:
                    yf_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={search_kw}&quotesCount=5&newsCount=0"
                    yf_headers = {'User-Agent': 'Mozilla/5.0'}
                    yf_res = requests.get(yf_url, headers=yf_headers, timeout=3)
                    if yf_res.status_code == 200:
                        for quote in yf_res.json().get('quotes', []):
                            sym = quote.get('symbol')
                            sname = quote.get('shortname', sym)
                            if sym and not sym.endswith('.KS') and not sym.endswith('.KQ'):
                                matches.append((sym, f"[해외] {sname}"))
                except:
                    pass
                
                def sort_key(x):
                    c, n = x
                    clean_n = n.replace(" ", "").replace("[해외]", "").strip().lower()
                    exact_match = 0 if clean_n == search_kw_clean or c.lower() == search_kw_clean else 1
                    return (exact_match, len(n), n)
                    
                matches.sort(key=sort_key)
                final_results = matches[:7]
                
                if final_results:
                    st.markdown("##### 💡 검색 결과 (오른쪽 버튼을 누르면 자동으로 폼에 추가됩니다)")
                    for code, name in final_results:
                        col_a, col_b = st.columns([5, 1])
                        with col_a: st.write(f"**{name}** (`{code}`)")
                        with col_b: st.button("➕ 추가", key=f"add_btn_{code}", on_click=append_to_etf_input, args=(code,))
                else:
                    st.warning("검색 결과가 없습니다.")

    with st.container(border=True):
        st.subheader("⚙️ 테스트 환경")
        
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 총 투자금 (원)", key="saved_cash", placeholder="5,000,000")
            period_input = st.text_input("백테스트 기간 (예: 2025 또는 2025.1~2026.4)", key="saved_period", placeholder="2025.1~2026.4")
        with col2:
            etf_input = st.text_input("종목 코드 (최대 4개, 배당풍차는 + 기호 사용)", key="saved_etf", placeholder="498400, 472150, 498400 + 472150")
            div_action_input = st.radio("배당금 처리", ["재투자", "인출(생활비)"], horizontal=True, key="saved_div_action")

        st.divider() 
        
        col3, col4 = st.columns(2)
        with col3:
            strategy_options = st.multiselect(
                "단일 종목 매수 방식 (복수 선택 가능)",
                ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                key="saved_strategy"
            )
        with col4:
            has_wm = '+' in etf_input
            strategy_options_wm = st.multiselect(
                "배당풍차 매수 방식 (복수 선택 가능)",
                ["일괄 매수", "분할 매수 (4분할)", "분할 매수 (6분할)"],
                key="saved_strategy_wm",
                disabled=not has_wm
            )
            
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)
        
        components.html(
            """
            <script>
            const doc = window.parent.document;
            if (!doc.window_enter_bound) {
                doc.window_enter_bound = true;
                doc.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter') {
                        const active = doc.activeElement;
                        if (active && active.tagName === 'INPUT') {
                            const ariaLabel = active.getAttribute('aria-label') || "";
                            if (ariaLabel.includes('검색어')) return; 
                        }
                        const buttons = Array.from(doc.querySelectorAll('button'));
                        const runBtn = buttons.find(b => b.innerText && b.innerText.includes('시뮬레이션 실행'));
                        if (runBtn) setTimeout(() => { runBtn.click(); }, 150);
                    }
                });
            }
            </script>
            """, height=0, width=0
        )

    if run_btn or st.session_state.do_run:
        st.session_state.do_run = False  
        
        st.session_state.last_inputs = {
            'cash': cash_input, 'period': period_input, 'div': div_action_input,
            'etf': etf_input, 'strat': strategy_options, 'strat_wm': strategy_options_wm
        }

        with st.spinner('배당풍차 및 주가 데이터를 통합 분석 중...'):
            safe_cash = cash_input if cash_input and cash_input.strip() else "5000000"
            clean_cash = re.sub(r'[^0-9.]', '', safe_cash)
            INITIAL_CASH = float(clean_cash) if clean_cash else 5000000.0
            
            safe_period = period_input if period_input and period_input.strip() else "2025.1~2026.4"
            safe_etf = etf_input if etf_input and etf_input.strip() else "498400, 472150, 498400 + 472150"
            
            try:
                if '~' in safe_period:
                    s_str, e_str = safe_period.split('~')
                    start_dt = pd.to_datetime(s_str.strip().replace('.', '-')) if '.' in s_str else pd.to_datetime(f"{s_str.strip()}-01-01")
                    end_dt = pd.to_datetime(e_str.strip().replace('.', '-')) + pd.offsets.MonthEnd(0) if '.' in e_str else pd.to_datetime(f"{e_str.strip()}-12-31")
                else:
                    if '.' in safe_period:
                        start_dt = pd.to_datetime(safe_period.strip().replace('.', '-'))
                        end_dt = start_dt + pd.offsets.MonthEnd(0)
                    else:
                        start_dt = pd.to_datetime(f"{safe_period.strip()}-01-01")
                        end_dt = pd.to_datetime(f"{safe_period.strip()}-12-31")
            except:
                start_dt, end_dt = pd.to_datetime("2025-01-01"), pd.to_datetime("2026-04-30")

            raw_target_strs = [t.strip().upper() for t in safe_etf.split(',') if t.strip()][:4]
            targets = []
            compare_keys = [] 
            
            strats_single = strategy_options if strategy_options else ["거치식 (일괄 매수)"]
            strats_wm = strategy_options_wm if strategy_options_wm else ["일괄 매수"]

            for t in raw_target_strs:
                if '+' in t:
                    for strat in strats_wm:
                        key = f"{t}_{strat}"
                        targets.append({'key': key, 'ticker': t, 'strategy': strat, 'name': f"풍차({t}) - {strat}"})
                        compare_keys.append(key)
                else:
                    for strat in strats_single:
                        key = f"{t}_{strat}"
                        targets.append({'key': key, 'ticker': t, 'strategy': strat, 'name': f"{get_stock_info(t)} ({strat})"})
                        compare_keys.append(key)

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
                st.error("종목 데이터를 불러올 수 없습니다. 코드를 다시 확인해 주세요.")
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
                is_windmill = len(t_tickers) > 1
                strat = target['strategy']
                
                is_windmill_split = is_windmill and strat in ["일괄 매수", "분할 매수 (4분할)", "분할 매수 (6분할)"]
                
                if any(tk not in processed_data for tk in t_tickers): continue
                
                if is_windmill_split:
                    if "4분할" in strat: N_splits = 4
                    elif "6분할" in strat: N_splits = 6
                    else: N_splits = 1
                    invest_dates_set = set() 
                else:
                    N_splits = 1
                    if strat == "거치식 (일괄 매수)": invest_dates_set = {all_trading_dates[0]}
                    elif strat == "적립식 (매일)": invest_dates_set = set(all_trading_dates)
                    elif strat == "적립식 (매주)": invest_dates_set = set(temp_s.groupby([temp_s.index.isocalendar().year, temp_s.index.isocalendar().week]).head(1).index)
                    else: invest_dates_set = set(temp_s.groupby([temp_s.index.year, temp_s.index.month]).head(1).index)

                installment = INITIAL_CASH / len(invest_dates_set) if len(invest_dates_set) > 0 else 0
                
                reserve_cash = 0.0 if is_windmill_split else INITIAL_CASH
                available_cash = 0.0
                staged_cash = INITIAL_CASH if is_windmill_split else 0.0 
                
                total_shares = 0
                total_withdrawn, total_dividend = 0.0, 0.0 
                history, summary, asset_by_date, monthly_data = [], [], {}, {}
                prev_asset = INITIAL_CASH
                reinvest_flag, windmill_swap_flag = False, False
                current_idx = 0
                current_ticker = t_tickers[current_idx]
                
                pending_dividends = {}
                scheduled_buys = {}

                # 💡 [핵심 알고리즘 수정] 배당락일 D-1에 마지막 분할 매수가 마감되도록 정교한 타임라인 세팅
                def schedule_buys(amount, from_idx, target_tk, n_split):
                    if amount <= 0: return
                    if n_split == 1:
                        scheduled_buys[all_trading_dates[from_idx]] = scheduled_buys.get(all_trading_dates[from_idx], 0.0) + amount
                        return
                    
                    b_div_series = processed_data[target_tk][1]
                    from_date = all_trading_dates[from_idx]
                    future_div_dates = b_div_series[(b_div_series.index > from_date) & (b_div_series > 0)].index
                    
                    if len(future_div_dates) > 0:
                        target_date = future_div_dates[0]
                        target_idx = all_trading_dates.index(target_date)
                        end_idx = max(from_idx, target_idx - 1) 
                    else:
                        end_idx = min(len(all_trading_dates)-1, from_idx + 20)
                        
                    if end_idx <= from_idx:
                        scheduled_buys[all_trading_dates[from_idx]] = scheduled_buys.get(all_trading_dates[from_idx], 0.0) + amount
                    else:
                        step = (end_idx - from_idx) / (n_split - 1)
                        cash_per = amount / n_split
                        for i in range(n_split):
                            # 마지막 회차는 무조건 end_idx (배당락일 전날 D-1)에 박히도록 보정
                            idx = end_idx if i == n_split - 1 else from_idx + int(round(i * step))
                            buy_date = all_trading_dates[idx]
                            scheduled_buys[buy_date] = scheduled_buys.get(buy_date, 0.0) + cash_per

                if is_windmill_split:
                    schedule_buys(INITIAL_CASH, 0, current_ticker, N_splits)

                for d_idx, date in enumerate(all_trading_dates):
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
                        
                        target_payout_date = date + pd.Timedelta(days=4)
                        valid_dates = [d for d in all_trading_dates if d >= target_payout_date]
                        actual_payout_date = valid_dates[0] if valid_dates else all_trading_dates[-1]
                        
                        if actual_payout_date not in pending_dividends:
                            pending_dividends[actual_payout_date] = []
                        pending_dividends[actual_payout_date].append({
                            'amount': div_amount, 'ticker': current_ticker, 'div_per_share': float(div), 'shares': int(total_shares)
                        })
                        if is_windmill: windmill_swap_flag = True

                    if date in pending_dividends:
                        for p_div in pending_dividends[date]:
                            amt = p_div['amount']
                            action_gubun = '배당금(입금)' if div_action_input == "재투자" else '배당금(인출)'
                            
                            if div_action_input == "재투자":
                                if is_windmill_split:
                                    staged_cash += amt
                                    scheduled_buys[date] = scheduled_buys.get(date, 0.0) + amt
                                else:
                                    available_cash += amt
                                    reinvest_flag = True
                            else:
                                total_withdrawn += amt

                            history.append({
                                '날짜': date.strftime('%Y/%m/%d'), '구분': action_gubun, '종목': p_div['ticker'], 
                                '단가': p_div['div_per_share'], '수량': p_div['shares'], '거래금액': amt, 
                                '현금잔고': float(reserve_cash + available_cash + staged_cash), 
                                '총자산': float(reserve_cash + available_cash + staged_cash + (total_shares * price))
                            })

                    is_invest_day = date in invest_dates_set
                    if not is_windmill_split and is_invest_day:
                        reserve_cash -= installment
                        available_cash += installment

                    if windmill_swap_flag:
                        sell_amount = total_shares * price
                        history.append({'날짜': date.strftime('%Y/%m/%d'), '구분': '풍차매도', '종목': current_ticker, '단가': float(price), '수량': int(total_shares), '거래금액': sell_amount, '현금잔고': float(reserve_cash + available_cash + staged_cash + sell_amount), '총자산': float(reserve_cash + available_cash + staged_cash + sell_amount)})
                        total_shares = 0
                        current_idx = (current_idx + 1) % len(t_tickers)
                        current_ticker = t_tickers[current_idx]
                        price = processed_data[current_ticker][0][date] 
                        
                        if is_windmill_split:
                            cash_to_reinvest = sell_amount + staged_cash
                            staged_cash = cash_to_reinvest
                            schedule_buys(cash_to_reinvest, d_idx, current_ticker, N_splits)
                        else:
                            available_cash += sell_amount
                            reinvest_flag = True
                            
                        windmill_swap_flag = False

                    if is_windmill_split:
                        if date in scheduled_buys:
                            buy_cash = min(scheduled_buys[date], staged_cash)
                            if buy_cash > 0 and not pd.isna(price):
                                shares_to_buy = int(buy_cash // price)
                                if shares_to_buy > 0:
                                    cost = shares_to_buy * price
                                    staged_cash -= cost
                                    total_shares += shares_to_buy
                                    gubun_text = f'매수({N_splits}분할)' if N_splits > 1 else '일괄매수'
                                    history.append({'날짜': date.strftime('%Y/%m/%d'), '구분': gubun_text, '종목': current_ticker, '단가': float(price), '수량': shares_to_buy, '거래금액': float(cost), '현금잔고': float(reserve_cash + available_cash + staged_cash), '총자산': float(reserve_cash + available_cash + staged_cash + (total_shares * price))})
                    else:
                        if is_invest_day or reinvest_flag:
                            if not pd.isna(price) and available_cash >= price:
                                shares_to_buy = int(available_cash // price)
                                if shares_to_buy > 0:
                                    cost = shares_to_buy * price
                                    available_cash -= cost
                                    total_shares += shares_to_buy
                                    gubun_text = '매수'
                                    if reinvest_flag and not is_invest_day: gubun_text = '풍차매수' if is_windmill else '배당재투자'
                                    elif reinvest_flag and is_invest_day: gubun_text = '매수+풍차' if is_windmill else '매수+재투자'
                                    history.append({'날짜': date.strftime('%Y/%m/%d'), '구분': gubun_text, '종목': current_ticker, '단가': float(price), '수량': shares_to_buy, '거래금액': float(cost), '현금잔고': float(reserve_cash + available_cash), '총자산': float(reserve_cash + available_cash + (total_shares * price))})
                            reinvest_flag = False
                    
                    cur_asset = float(reserve_cash + available_cash + staged_cash + (total_shares * price))
                    if date in eom_dates_set and date != all_trading_dates[-1]:
                        history.append({'날짜': date.strftime('%Y/%m/%d'), '구분': '월말평가', '종목': current_ticker, '단가': float(price), '수량': int(total_shares), '거래금액': 0.0, '현금잔고': float(reserve_cash + available_cash + staged_cash), '총자산': cur_asset})
                    
                    monthly_data[month_str]['end_asset'] = cur_asset
                    monthly_data[month_str]['end_price'] = float(price)
                    label = date.strftime('%Y/%m/%d')
                    if label in chart_labels:
                        asset_by_date[label] = cur_asset
                        summary.append({'기간': label, '기말단가': float(price), '기말자산': cur_asset, '증감': float(cur_asset - prev_asset), '수익률': float(((cur_asset / INITIAL_CASH) - 1) * 100)})
                        prev_asset = cur_asset

                chart_vals = [asset_by_date.get(lbl, INITIAL_CASH) for lbl in chart_labels]
                last_date = all_trading_dates[-1]
                last_price = float(processed_data[current_ticker][0][last_date])
                final_eval_asset = float(reserve_cash + available_cash + staged_cash + (total_shares * last_price))
                history.append({'날짜': last_date.strftime('%Y/%m/%d'), '구분': '최종평가', '종목': current_ticker, '단가': last_price, '수량': int(total_shares), '거래금액': 0.0, '현금잔고': float(reserve_cash + available_cash + staged_cash), '총자산': final_eval_asset})

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
    colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#6366f1', '#14b8a6']
    for idx, k in enumerate(res['compare_keys']):
        d = res['all_data'][k]
        datasets.append({'label': d['name'], 'data': d['chart_values'], 'borderColor': colors[idx % len(colors)], 'tension': 0.3, 'fill': False})

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
        tbody tr:nth-child(even) {{ background-color: #f8fafc; }}
        tbody tr:hover {{ background-color: #f1f5f9; transition: background-color 0.2s ease; }}
        .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; min-width: 45px; text-align: center;}}
        .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .div {{ background: #10b981; }} .withdraw {{ background: #f59e0b; }} .reinvest {{ background: #8b5cf6; }} .eval {{ background: #64748b; }} .eval-month {{ background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1; }}
        .header-flex {{ display: flex; justify-content: space-between; align-items: center; margin: 25px 0 10px 0; }}
        .sort-select {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #cbd5e1; font-size: 13px; background: white; font-weight: 600; color: #475569; outline: none; cursor: pointer; }}
        .section-icon {{ border-left: 3px solid #3b82f6; padding-left: 8px; }}
    </style>
    </head><body>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>
    <div class="card-grid" id="stat-cards"></div>
    <div style="margin-bottom: 15px; display:flex; justify-content:flex-end;"><select id="ticker-select" class="sort-select" style="min-width: 250px;" onchange="renderTable()"></select></div>
    <div class="header-flex"><div style="display:flex; align-items:center; gap:10px;"><span class="section-icon" style="font-weight:700; font-size:16px;">🗓️ 월별 요약</span></div><select id="sort-select-monthly" class="sort-select" onchange="renderTable()"><option value="asc">과거순</option><option value="desc">최신순</option></select></div>
    <div class="table-wrapper"><table><thead><tr><th>기간</th><th>주당배당</th><th>배당률</th><th>배당합계</th><th>기말자산</th><th>증감</th></tr></thead><tbody id="monthly-tbody"></tbody></table></div>
    <div class="header-flex"><div style="display:flex; align-items:center; gap:10px;"><span class="section-icon" style="font-weight:700; font-size:16px;">🔍 상세 거래 내역</span></div><select id="sort-select-history" class="sort-select" onchange="renderTable()"><option value="asc">과거순</option><option value="desc">최신순</option></select></div>
    <div class="table-wrapper"><table><thead><tr><th>날짜</th><th>구분</th><th>종목</th><th>단가/분배금</th><th>수량</th><th>금액</th><th>현금잔고</th><th>총자산</th></tr></thead><tbody id="tbody"></tbody></table></div>
    <script>
        const data = {json.dumps(res['all_data'])};
        const keys = {json.dumps(res['compare_keys'])};
        const labels = {json.dumps(res['labels'])};
        const sel = document.getElementById('ticker-select');
        keys.forEach(k => sel.add(new Option(data[k].name, k)));
        function fmt(v) {{ return Math.floor(v).toLocaleString() + "원"; }}
        function fmtMan(v) {{ if (v === 0) return "0"; const isNeg = v < 0; let absV = Math.abs(v); if (absV < 10000) return (isNeg ? "-" : "") + Math.floor(absV).toLocaleString() + "원"; return (isNeg ? "-" : "") + Math.floor(absV / 10000).toLocaleString() + "만"; }}
        function colorForChange(v) {{ return v > 0 ? '#dc2626' : (v < 0 ? '#2563eb' : '#334155'); }}
        function getBadgeClass(type) {{ if(type.includes('풍차매도')) return 'sell'; if(type.includes('매수')) return 'reinvest'; if(type.includes('월말평가')) return 'eval-month'; if(type.includes('배당금(인출)')) return 'withdraw'; if(type.includes('배당금(입금)')) return 'div'; if(type.includes('최종평가')) return 'eval'; return 'buy'; }}
        function renderTable() {{
            const k = sel.value; const d = data[k];
            document.getElementById('stat-cards').innerHTML = keys.map(key => {{
                const item = data[key]; const isWithdrawal = item.div_action === '인출(생활비)'; const assetLabel = isWithdrawal ? '평가 자산' : '최종 자산';
                let withdrawRow = isWithdrawal ? `<div class="card-row"><span>누적 인출금</span><span style="color:#10b981; font-weight:600;">+${{fmt(item.total_withdrawn)}}</span></div>` : '';
                return `<div class="card" style="border-top-color: ${{key===k?'#ef4444':'#94a3b8'}}"><h3>${{item.name}}</h3><div class="card-row"><span>초기 투자금</span><strong>${{fmt(item.initial_cash)}}</strong></div><div class="card-row"><span>총 배당금</span><span style="color:#d97706; font-weight:600;">+${{fmt(item.total_dividend)}}</span></div><div class="card-row"><span>${{assetLabel}}</span><strong>${{fmt(item.final_asset)}}</strong></div>${{withdrawRow}}<div class="card-row"><span>총 수익금</span><span style="color:${{item.total_profit>=0?'#dc2626':'#2563eb'}}; font-weight:600;">${{item.total_profit>=0?'+':''}}${{fmt(item.total_profit)}} (${{item.profit_rate.toFixed(2)}}%)</span></div></div>`;
            }}).join('');
            let monthlyData = d.monthly_summary.slice(); if (document.getElementById('sort-select-monthly').value === 'desc') monthlyData.reverse(); 
            document.getElementById('monthly-tbody').innerHTML = monthlyData.map(m => `<tr><td>${{m.기간}}</td><td>${{Math.floor(m.주당배당).toLocaleString()}}</td><td style="color:#d97706; font-weight:600;">${{m.배당률.toFixed(2)}}%</td><td>${{m.배당합계 > 0 ? fmtMan(m.배당합계) : '-'}}</td><td style="font-weight:600;">${{fmtMan(m.기말자산)}}</td><td style="color:${{colorForChange(m.증감)}}; font-weight:600;">${{m.증감 > 0 ? '+' : ''}}${{fmtMan(m.증감)}}</td></tr>`).join('');
            let historyData = d.history.slice(); if (document.getElementById('sort-select-history').value === 'desc') historyData.reverse(); 
            document.getElementById('tbody').innerHTML = historyData.map(h => `<tr><td>${{h.날짜}}</td><td><span class="badge ${{getBadgeClass(h.구분)}}">${{h.구분}}</span></td><td style="color:#64748b; font-weight:600;">${{h.종목}}</td><td>${{fmt(h.단가)}}</td><td>${{h.수량.toLocaleString()}}</td><td>${{h.거래금액 > 0 ? fmt(h.거래금액) : '-'}}</td><td>${{fmt(h.현금잔고)}}</td><td><strong>${{fmt(h.총자산)}}</strong></td></tr>`).join('');
        }}
        new Chart(document.getElementById('assetChart'), {{ type: 'line', data: {{ labels: labels, datasets: {json.dumps(datasets)} }}, options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ ticks: {{ callback: v => (v/10000) + '만' }} }} }} }} }});
        renderTable();
    </script></body></html>
    """
    components.html(html_code, height=2000, scrolling=True)

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
st.set_page_config(page_title="해외 ETF 시뮬레이터", layout="wide")

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
def get_stock_info(code):
    """야후 파이낸스에서 종목명 가져오기"""
    if not code: return ""
    try:
        ticker = yf.Ticker(code)
        name = ticker.info.get('shortName', code)
        return f"{name}({code.upper()})"
    except:
        return code.upper()

def fetch_prices(code, start_date, end_date):
    """야후 파이낸스 가격 데이터 수집"""
    try:
        ticker = yf.Ticker(code)
        df = ticker.history(start=start_date, end=end_date)
        if df.empty: return pd.Series(dtype=float)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close']
    except:
        return pd.Series(dtype=float)

# ==========================================
# UI 영역
# ==========================================
st.title("🌎 해외 ETF 시뮬레이터")

if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 시뮬레이션 설정 다시 하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 시뮬레이션 설정")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("초기 총 투자금 ($)", "100000")
            period_input = st.text_input("백테스트 기간 (예: 2023~2024)", "2023~2024")
            
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                currency_option = st.radio("결과 표시 통화", ["USD ($)", "KRW (원)"], horizontal=True)
            with col_c2:
                exchange_rate = st.number_input("적용 환율 (원/$)", value=1400, step=10)

        with col2:
            etf_input = st.text_input("종목 티커 (쉼표 구분)", "QQQ")
            strategy_options = st.multiselect(
                "분할 매수 방식 (※ 단일 종목 입력 시에만 비교 적용)",
                ["거치식 (일괄 매수)", "적립식 (매일)", "적립식 (매주)", "적립식 (매월)"],
                default=["거치식 (일괄 매수)", "적립식 (매월)"]
            )
            
        run_btn = st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('미국 시장 데이터 분석 중...'):
            try:
                INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            except:
                INITIAL_CASH = 100000.0
            
            try:
                if '~' in period_input:
                    s_str, e_str = period_input.split('~')
                    start_dt = pd.to_datetime(s_str.strip() if '.' in s_str else f"{s_str.strip()}-01-01")
                    end_dt = pd.to_datetime(e_str.strip() if '.' in e_str else f"{e_str.strip()}-12-31")
                else:
                    start_dt = pd.to_datetime(f"{period_input.strip()}-01-01")
                    end_dt = pd.to_datetime(f"{period_input.strip()}-12-31")
            except:
                start_dt, end_dt = pd.to_datetime("2023-01-01"), pd.to_datetime("2023-12-31")

            tickers = [t.strip().upper() for t in etf_input.replace(',', ' ').split() if t.strip()]
            if not tickers: tickers = ["QQQ"]

            if "거치식 (일괄 매수)" not in strategy_options:
                strategy_options.insert(0, "거치식 (일괄 매수)")

            targets = []
            if len(tickers) == 1:
                compare_keys = strategy_options
                for strat in strategy_options:
                    targets.append({'key': strat, 'ticker': tickers[0], 'strategy': strat, 'name': f"{get_stock_info(tickers[0])} - {strat}"})
                st.session_state.display_title = f"### 📊 {period_input} {get_stock_info(tickers[0])} 투자 방식 비교"
            else:
                compare_keys = tickers
                for t in tickers:
                    targets.append({'key': t, 'ticker': t, 'strategy': "거치식 (일괄 매수)", 'name': get_stock_info(t)})
                st.session_state.display_title = f"### 📊 {period_input} 해외 ETF 종목 비교 (거치식)"

            all_sim_data = {}
            chart_labels = []
            
            for target in targets:
                t_key = target['key']
                t_code = target['ticker']
                strat = target['strategy']
                name = target['name']
                
                prices = fetch_prices(t_code, start_dt, end_dt)
                if prices.empty: continue
                
                if strat == "거치식 (일괄 매수)":
                    invest_dates = [prices.index[0]]
                elif strat == "적립식 (매일)":
                    invest_dates = prices.index
                elif strat == "적립식 (매주)":
                    invest_dates = prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week]).head(1).index
                elif strat == "적립식 (매월)":
                    invest_dates = prices.groupby([prices.index.year, prices.index.month]).head(1).index
                else:
                    invest_dates = [prices.index[0]]

                N_invest = len(invest_dates)
                installment = INITIAL_CASH / N_invest if N_invest > 0 else 0
                invest_dates_set = set(invest_dates)
                
                reserve_cash = INITIAL_CASH 
                available_cash = 0.0        
                total_shares = 0
                
                history = []
                summary = []
                ticker_chart_values = []
                prev_asset = INITIAL_CASH
                
                # 주별(Weekly) 그룹핑으로 변경
                weekly_groups = prices.groupby([prices.index.isocalendar().year, prices.index.isocalendar().week])
                
                for (y, w), group in weekly_groups:
                    eow_dt = group.index[-1]
                    eow_price = float(group.iloc[-1])
                    
                    for date, price in group.items():
                        if date in invest_dates_set:
                            reserve_cash -= installment
                            available_cash += installment
                            
                            shares_to_buy = int(available_cash // float(price))
                            if shares_to_buy > 0:
                                available_cash -= shares_to_buy * float(price)
                                total_shares += shares_to_buy
                                
                                history.append({
                                    '날짜': date.strftime('%Y/%m/%d'),
                                    '구분': '매수' if strat == "거치식 (일괄 매수)" else '분할매수',
                                    '단가': float(price),
                                    '수량': shares_to_buy,
                                    '거래금액': float(shares_to_buy * price),
                                    '현금잔고': float(reserve_cash + available_cash), 
                                    '총자산': float(reserve_cash + available_cash + (total_shares * price))
                                })
                    
                    current_asset = float(reserve_cash + available_cash + (total_shares * eow_price))
                    
                    # 주별 라벨 생성 (예: 2023-W01)
                    label = f"{y}-W{w:02d}"
                    if label not in chart_labels: chart_labels.append(label)
                    ticker_chart_values.append(current_asset)
                    
                    summary.append({
                        '기간': f"{y}-W{w:02d}",
                        '기말단가': eow_price,
                        '기말자산': current_asset,
                        '증감': float(current_asset - prev_asset),
                        '수익률': float(((current_asset / INITIAL_CASH) - 1) * 100)
                    })
                    
                    history.append({
                        '날짜': eow_dt.strftime('%Y/%m/%d'),
                        '구분': '평가',
                        '단가': eow_price,
                        '수량': int(total_shares),
                        '거래금액': 0.0,
                        '현금잔고': float(reserve_cash + available_cash),
                        '총자산': current_asset
                    })
                    prev_asset = current_asset
                
                all_sim_data[t_key] = {
                    'name': name,
                    'summary': summary,
                    'history': history,
                    'chart_values': ticker_chart_values,
                    'final_asset': prev_asset,
                    'total_profit': prev_asset - INITIAL_CASH,
                    'profit_rate': ((prev_asset / INITIAL_CASH) - 1) * 100
                }

            st.session_state.sim_result_data = {
                'initial_cash': INITIAL_CASH,
                'compare_keys': compare_keys,
                'labels': chart_labels,
                'all_data': all_sim_data,
                'currency_option': currency_option,
                'exchange_rate': exchange_rate
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

    datasets = []
    colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4']
    for idx, t_key in enumerate(res['compare_keys']):
        if t_key in res['all_data']:
            t_data = res['all_data'][t_key]
            datasets.append({
                'label': t_data['name'],
                'data': t_data['chart_values'],
                'borderColor': colors[idx % len(colors)],
                'tension': 0.4, # 곡선을 부드럽게 (기존 0.1 -> 0.4)
                'fill': False
            })

    disp_currency_txt = "($)" if res['currency_option'] == "USD ($)" else "(원)"

    html_code = f"""
    <!DOCTYPE html><html><head><meta charset="utf-8"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; }}
        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 25px; }}
        .card {{ background: white; padding: 18px 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-top: 4px solid #94a3b8; text-align: left; }}
        .card h3 {{ font-size: 14px; margin: 0 0 15px 0; color: #1e293b; font-weight:700; line-height: 1.4; word-break: keep-all; }}
        .card-row {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 8px; color: #64748b; }}
        .card-row.bold {{ font-weight: 600; color: #334155; }}
        .card-divider {{ border-top: 1px dashed #cbd5e1; margin: 12px 0; }}
        .card-total {{ display: flex; justify-content: space-between; font-size: 15px; font-weight: 800; color: #0f172a; align-items: center; }}
        .section-title {{ font-size: 16px; font-weight: 700; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
        .header-flex {{ display: flex; align-items: center; justify-content: space-between; margin: 25px 0 10px 0; }}
        .sort-select {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #cbd5e1; font-size: 13px; background: white; font-weight: 600; color: #475569; outline: none; cursor: pointer; }}
        .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 380px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
        .table-wrapper {{ overflow-x: auto; background: white; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
        table {{ width: 100%; border-collapse: collapse; min-width: 600px; }}
        th {{ background: #f8fafc; padding: 12px; font-size: 12px; border-bottom: 2px solid #e2e8f0; color: #475569; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; font-size: 13px; color: #334155; }}
        .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; }}
        .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .eval {{ background: #94a3b8; }}
    </style>
    </head><body>
    
    <div class="section-title">📈 자산 성장 비교 {disp_currency_txt}</div>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>

    <div class="card-grid" id="stat-cards"></div>
    
    <div class="header-flex">
        <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-weight:700; font-size:16px;">📅 주별 요약</span>
            <select id="ticker-select-summary" class="sort-select" onchange="renderTables()"></select>
        </div>
        <select id="sort-select-summary" class="sort-select" onchange="renderTables()">
            <option value="desc">최신순</option>
            <option value="asc">과거순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table><thead><tr><th>기간</th><th>기말단가</th><th>기말자산</th><th>증감</th><th>누적수익률</th></tr></thead>
        <tbody id="summary-tbody"></tbody></table>
    </div>

    <div class="header-flex">
        <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-weight:700; font-size:16px;">🔍 상세 거래 내역</span>
            <select id="ticker-select-history" class="sort-select" onchange="renderTables()"></select>
        </div>
        <select id="sort-select-history" class="sort-select" onchange="renderTables()">
            <option value="asc">과거순</option>
            <option value="desc">최신순</option>
        </select>
    </div>
    <div class="table-wrapper">
        <table><thead><tr><th>날짜</th><th>구분</th><th>단가</th><th>수량</th><th>거래금액</th><th>잔고(대기자금 포함)</th><th>총자산</th></tr></thead>
        <tbody id="history-tbody"></tbody></table>
    </div>

    <script>
        const allData = {json.dumps(res['all_data'])};
        const compareKeys = {json.dumps(res['compare_keys'])};
        const labels = {json.dumps(res['labels'])};
        const displayCurrency = "{res['currency_option']}";
        const exRate = {res['exchange_rate']};
        const initialCash = {res['initial_cash']};

        const tSelSum = document.getElementById('ticker-select-summary');
        const tSelHis = document.getElementById('ticker-select-history');
        compareKeys.forEach(t => {{
            if(allData[t]) {{
                let opt1 = new Option(allData[t].name, t);
                let opt2 = new Option(allData[t].name, t);
                tSelSum.add(opt1); tSelHis.add(opt2);
            }}
        }});

        function fmtMoney(val) {{
            if (val === 0 || val === '0') return displayCurrency === "USD ($)" ? "$0.00" : "0원";
            let num = Number(val);
            if (displayCurrency === "KRW (원)") {{
                num = num * exRate;
                if (Math.abs(num) >= 10000) {{
                    return Math.floor(num / 10000).toLocaleString() + "만 원";
                }}
                return Math.floor(num).toLocaleString() + "원";
            }} else {{
                return "$" + num.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
            }}
        }}

        function renderTables() {{
            const targetSum = tSelSum.value;
            const targetHis = tSelHis.value;
            const sortSum = document.getElementById('sort-select-summary').value;
            const sortHis = document.getElementById('sort-select-history').value;

            document.getElementById('stat-cards').innerHTML = compareKeys.map(t => {{
                if(!allData[t]) return "";
                const d = allData[t];
                const profitColor = d.total_profit >= 0 ? '#dc2626' : '#2563eb';
                const sign = d.total_profit > 0 ? '+' : '';
                
                return `<div class="card" style="border-top-color: ${{getTickerColor(t)}}">
                    <h3>${{d.name}}</h3>
                    
                    <div class="card-row">
                        <span>초기 투자금</span>
                        <span class="bold">${{fmtMoney(initialCash)}}</span>
                    </div>
                    
                    <div class="card-row">
                        <span>수익금</span>
                        <span style="font-weight: 600; color: ${{profitColor}};">
                            ${{sign}}${{fmtMoney(d.total_profit)}} (${{d.profit_rate.toFixed(2)}}%)
                        </span>
                    </div>
                    
                    <div class="card-divider"></div>
                    
                    <div class="card-total">
                        <span>최종 자산</span>
                        <span style="color: #dc2626; font-size: 18px;">${{fmtMoney(d.final_asset)}}</span>
                    </div>
                </div>`;
            }}).join('');

            let sData = [...allData[targetSum].summary];
            if(sortSum === 'desc') sData.reverse();
            document.getElementById('summary-tbody').innerHTML = sData.map(s => `
                <tr>
                    <td>${{s.기간}}</td>
                    <td>${{fmtMoney(s.기말단가)}}</td>
                    <td><b>${{fmtMoney(s.기말자산)}}</b></td>
                    <td style="color:${{s.증감 >=0 ? '#dc2626':'#2563eb'}}; font-weight:600;">${{s.증감 > 0 ? '+' : ''}}${{fmtMoney(s.증감)}}</td>
                    <td style="color:${{s.수익률 >=0 ? '#dc2626':'#2563eb'}}; font-weight:600;">${{s.수익률.toFixed(2)}}%</td>
                </tr>
            `).join('');

            let hData = [...allData[targetHis].history];
            if(sortHis === 'desc') hData.reverse();
            document.getElementById('history-tbody').innerHTML = hData.map(h => `
                <tr>
                    <td>${{h.날짜}}</td>
                    <td><span class="badge ${{h.구분.includes('매수') ? 'buy' : 'eval'}}">${{h.구분}}</span></td>
                    <td>${{fmtMoney(h.단가)}}</td>
                    <td>${{h.수량.toLocaleString()}}</td>
                    <td>${{h.거래금액 > 0 ? fmtMoney(h.거래금액) : '-'}}</td>
                    <td>${{fmtMoney(h.현금잔고)}}</td>
                    <td style="font-weight:700;">${{fmtMoney(h.총자산)}}</td>
                </tr>
            `).join('');
        }}

        function getTickerColor(ticker) {{
            const idx = compareKeys.indexOf(ticker);
            const colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4'];
            return colors[idx % colors.length];
        }}

        let chartDatasets = {json.dumps(datasets)};
        if (displayCurrency === "KRW (원)") {{
            chartDatasets = chartDatasets.map(ds => {{
                ds.data = ds.data.map(val => val * exRate);
                return ds;
            }});
        }}

        new Chart(document.getElementById('assetChart'), {{
            type: 'line',
            data: {{ labels: labels, datasets: chartDatasets }},
            options: {{ 
                responsive: true, 
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                plugins: {{
                    legend: {{ position: 'top', labels: {{ usePointStyle: true, boxWidth: 8 }} }}
                }},
                scales: {{ 
                    y: {{ 
                        grid: {{ color: '#f1f5f9' }},
                        ticks: {{ 
                            color: '#64748b',
                            callback: function(value) {{ 
                                if (displayCurrency === "KRW (원)") {{
                                    return (value / 10000).toLocaleString() + '만'; 
                                }} else {{
                                    return '$' + value.toLocaleString(); 
                                }}
                            }} 
                        }} 
                    }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: '#64748b' }} }}
                }}
            }}
        }});

        renderTables();
    </script>
    </body></html>
    """
    components.html(html_code, height=2500, scrolling=True)

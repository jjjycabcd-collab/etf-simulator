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
st.set_page_config(page_title="해외 ETF 비교 백테스트", layout="wide")

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
        if df.empty: return pd.Series()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df['Close']
    except:
        return pd.Series()

def fmt_usd(val):
    return f"${val:,.2f}"

# ==========================================
# UI 영역
# ==========================================
st.title("🌎 해외 ETF 비교 백테스트")

if st.session_state.run_clicked and not st.session_state.show_settings:
    if st.button("⚙️ 시뮬레이션 설정 다시 하기", use_container_width=True):
        st.session_state.show_settings = True
        st.rerun()

if st.session_state.show_settings:
    with st.container(border=True):
        st.subheader("⚙️ 시뮬레이션 설정")
        col1, col2 = st.columns(2)
        with col1:
            cash_input = st.text_input("종목당 초기 투자금 ($)", "100000")
            period_input = st.text_input("백테스트 기간 (예: 2023~2024)", "2023~2024")
        with col2:
            etf_input = st.text_input("종목 티커 (쉼표 구분)", "QQQ, SPY, DIA")
            st.info("※ 각 종목에 설정한 투자금을 동일하게 각각 투자하여 비교합니다.")
            
        run_btn = st.button("🚀 비교 시뮬레이션 실행", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner('미국 시장 데이터 분석 중...'):
            INITIAL_CASH = float(re.sub(r'[^0-9.]', '', cash_input))
            
            # 기간 파싱
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
            
            all_sim_data = {}
            chart_labels = []
            
            for ticker_code in tickers:
                prices = fetch_prices(ticker_code, start_dt, end_dt)
                if prices.empty: continue
                
                name = get_stock_info(ticker_code)
                
                # 첫 거래일 매수
                start_price = float(prices.iloc[0])
                shares = INITIAL_CASH // start_price
                rem_cash = INITIAL_CASH % start_price
                
                history = []
                summary = []
                
                # 매수 기록
                history.append({
                    '날짜': prices.index[0].strftime('%Y/%m/%d'),
                    '구분': '매수',
                    '단가': start_price,
                    '수량': int(shares),
                    '거래금액': shares * start_price,
                    '현금잔고': rem_cash,
                    '총자산': INITIAL_CASH
                })
                
                # 월별 요약 및 차트 데이터 생성을 위한 처리
                monthly_groups = prices.groupby([prices.index.year, prices.index.month])
                prev_asset = INITIAL_CASH
                
                ticker_chart_values = []
                
                for (y, m), group in monthly_groups:
                    eom_dt = group.index[-1]
                    eom_price = float(group[-1])
                    current_asset = rem_cash + (shares * eom_price)
                    
                    # 차트용 데이터 (월말 기준)
                    label = f"{y}.{m}"
                    if label not in chart_labels: chart_labels.append(label)
                    ticker_chart_values.append(current_asset)
                    
                    summary.append({
                        '기간': f"{y}.{m:02d}",
                        '기말단가': eom_price,
                        '기말자산': current_asset,
                        '증감': current_asset - prev_asset,
                        '수익률': ((current_asset / INITIAL_CASH) - 1) * 100
                    })
                    
                    history.append({
                        '날짜': eom_dt.strftime('%Y/%m/%d'),
                        '구분': '평가',
                        '단가': eom_price,
                        '수량': int(shares),
                        '거래금액': 0,
                        '현금잔고': rem_cash,
                        '총자산': current_asset
                    })
                    prev_asset = current_asset
                
                all_sim_data[ticker_code] = {
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
                'tickers': tickers,
                'labels': chart_labels,
                'all_data': all_sim_data
            }
            st.session_state.display_title = f"### 📊 {period_input} 해외 ETF 비교 결과"
            st.session_state.run_clicked = True
            st.session_state.show_settings = False
            st.rerun()

# ==========================================
# 결과 출력 영역
# ==========================================
if st.session_state.run_clicked and st.session_state.sim_result_data:
    res = st.session_state.sim_result_data
    st.markdown(st.session_state.display_title)

    # 차트용 데이터 구성
    datasets = []
    colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4']
    for idx, (t_code, t_data) in enumerate(res['all_data'].items()):
        datasets.append({
            'label': t_data['name'],
            'data': t_data['chart_values'],
            'borderColor': colors[idx % len(colors)],
            'tension': 0.1,
            'fill': False
        })

    html_code = f"""
    <!DOCTYPE html><html><head><meta charset="utf-8"><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #f8fafc; padding: 10px; color: #334155; }}
        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 25px; }}
        .card {{ background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-top: 4px solid #94a3b8; }}
        .card h3 {{ font-size: 12px; margin: 0 0 8px 0; color: #64748b; }}
        .card p {{ font-size: 15px; margin: 0; font-weight:700; }}
        .section-title {{ font-size: 16px; font-weight: 700; margin: 30px 0 12px 0; border-left: 4px solid #3b82f6; padding-left: 8px; }}
        .header-flex {{ display: flex; align-items: center; justify-content: space-between; margin: 25px 0 10px 0; }}
        .sort-select {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #cbd5e1; font-size: 13px; background: white; font-weight: 600; color: #475569; }}
        .chart-container {{ background: white; padding: 15px; border-radius: 12px; height: 350px; margin-bottom: 20px; }}
        .table-wrapper {{ overflow-x: auto; background: white; border-radius: 12px; }}
        table {{ width: 100%; border-collapse: collapse; min-width: 600px; }}
        th {{ background: #f1f5f9; padding: 12px; font-size: 12px; border-bottom: 2px solid #e2e8f0; }}
        td {{ padding: 10px; border-bottom: 1px solid #f1f5f9; text-align: center; font-size: 12px; }}
        .badge {{ padding: 4px 6px; border-radius: 4px; color: white; font-size: 11px; font-weight: 600; display: inline-block; }}
        .buy {{ background: #ef4444; }} .sell {{ background: #3b82f6; }} .eval {{ background: #94a3b8; }}
    </style>
    </head><body>
    
    <div class="section-title">📈 자산 성장 비교 ($)</div>
    <div class="chart-container"><canvas id="assetChart"></canvas></div>

    <div class="card-grid" id="stat-cards">
        </div>
    
    <div class="header-flex">
        <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-weight:700; font-size:16px;">📅 월별 요약</span>
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
        <table><thead><tr><th>날짜</th><th>구분</th><th>단가</th><th>수량</th><th>거래금액</th><th>잔고</th><th>총자산</th></tr></thead>
        <tbody id="history-tbody"></tbody></table>
    </div>

    <script>
        const allData = {json.dumps(res['all_data'])};
        const tickers = {json.dumps(res['tickers'])};
        const labels = {json.dumps(res['labels'])};

        // 초기 셀렉트박스 세팅
        const tSelSum = document.getElementById('ticker-select-summary');
        const tSelHis = document.getElementById('ticker-select-history');
        tickers.forEach(t => {{
            if(allData[t]) {{
                let opt1 = new Option(allData[t].name, t);
                let opt2 = new Option(allData[t].name, t);
                tSelSum.add(opt1); tSelHis.add(opt2);
            }}
        }});

        function fmtUsd(val) {{
            return "$" + Number(val).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
        }}

        function renderTables() {{
            const targetSum = tSelSum.value;
            const targetHis = tSelHis.value;
            const sortSum = document.getElementById('sort-select-summary').value;
            const sortHis = document.getElementById('sort-select-history').value;

            // 요약 카드 렌더링 (전 종목)
            document.getElementById('stat-cards').innerHTML = tickers.map(t => {{
                if(!allData[t]) return "";
                const d = allData[t];
                return `<div class="card" style="border-top-color: ${{getTickerColor(t)}}">
                    <h3>${{d.name}}</h3>
                    <p style="color:#dc2626">최종: ${{fmtUsd(d.final_asset)}}</p>
                    <div style="font-size:12px; margin-top:5px; font-weight:600;">
                        수익: <span style="color:${{d.total_profit >=0 ? '#dc2626':'#2563eb'}}">${{fmtUsd(d.total_profit)}} (${{d.profit_rate.toFixed(2)}}%)</span>
                    </div>
                </div>`;
            }}).join('');

            // 월별 요약 테이블
            let sData = [...allData[targetSum].summary];
            if(sortSum === 'desc') sData.reverse();
            document.getElementById('summary-tbody').innerHTML = sData.map(s => `
                <tr>
                    <td>${{s.기간}}</td>
                    <td>${{fmtUsd(s.기말단가)}}</td>
                    <td><b>${{fmtUsd(s.기말자산)}}</b></td>
                    <td style="color:${{s.증감 >=0 ? '#dc2626':'#2563eb'}}; font-weight:600;">${{fmtUsd(s.증감)}}</td>
                    <td style="color:${{s.수익률 >=0 ? '#dc2626':'#2563eb'}}; font-weight:600;">${{s.수익률.toFixed(2)}}%</td>
                </tr>
            `).join('');

            // 상세 내역 테이블
            let hData = [...allData[targetHis].history];
            if(sortHis === 'desc') hData.reverse();
            document.getElementById('history-tbody').innerHTML = hData.map(h => `
                <tr>
                    <td>${{h.날짜}}</td>
                    <td><span class="badge ${{h.구분 === '매수' ? 'buy' : 'eval'}}">${{h.구분}}</span></td>
                    <td>${{fmtUsd(h.단가)}}</td>
                    <td>${{h.수량.toLocaleString()}}</td>
                    <td>${{h.거래금액 > 0 ? fmtUsd(h.거래금액) : '-'}}</td>
                    <td>${{fmtUsd(h.현금잔고)}}</td>
                    <td style="font-weight:700;">${{fmtUsd(h.총자산)}}</td>
                </tr>
            `).join('');
        }}

        function getTickerColor(ticker) {{
            const idx = tickers.indexOf(ticker);
            const colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4'];
            return colors[idx % colors.length];
        }}

        // 차트 렌더링
        new Chart(document.getElementById('assetChart'), {{
            type: 'line',
            data: {{ labels: labels, datasets: {json.dumps(datasets)} }},
            options: {{ 
                responsive: true, 
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                scales: {{ y: {{ ticks: {{ callback: function(value) {{ return '$' + value.toLocaleString(); }} }} }} }}
            }}
        }});

        renderTables();
    </script>
    </body></html>
    """
    components.html(html_code, height=2500, scrolling=True)

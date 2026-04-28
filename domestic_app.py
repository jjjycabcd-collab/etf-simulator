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

# --- 👇 추가할 부분: 입력값 유지를 위한 세션 상태 변수 세팅 👇 ---
if 'saved_cash' not in st.session_state: st.session_state.saved_cash = "5,000,000"
if 'saved_period' not in st.session_state: st.session_state.saved_period = "2025.1~2026.4"
if 'saved_div_action' not in st.session_state: st.session_state.saved_div_action = "재투자"
if 'saved_etf' not in st.session_state: st.session_state.saved_etf = "498400, 472150, 498400 + 472150"
if 'saved_strategy' not in st.session_state: st.session_state.saved_strategy = ["거치식 (일괄 매수)"]
# ----------------------------------------------------------------

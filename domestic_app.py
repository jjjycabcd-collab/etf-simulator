import sys
import pandas as pd
import re
import json
import datetime
import requests
import io
import yfinance as yf

# 👇 아래 두 줄이 반드시 포함되어야 합니다! 👇
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 웹 페이지 기본 설정 및 상태 초기화
# ==========================================
st.set_page_config(page_title="월배당 ETF 백테스트", layout="wide")
# ... (이하 기존 코드 동일)

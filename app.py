import io
import re
import time
import zipfile
from datetime import datetime

import google.generativeai as genai
import pandas as pd
from openpyxl.styles import Border, Font, PatternFill, Side
from PIL import Image
from pypdf import PdfReader, PdfWriter
import streamlit as st

# ==============================================================================
# KONSTANTEN & CONFIG
# ==============================================================================
PAGE_TITLE = "Beleg-Flow & DATEV Parser Pro AI"
PAGE_ICON = "🧾"
GEMINI_MODEL = "gemini-3.1-flash-lite"
FREE_TIER_DELAY = 4.2
MWST_19_FACTOR = 19 / 119
MWST_7_FACTOR = 7 / 107

MIME_MAP = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte", "Bankeinzug", "Überweisung", "PayPal", "Bar"]
_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

# ==============================================================================
# UI SETUP
# ==============================================================================
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.markdown("""<style>[data-testid="stSidebarNav"] {display: none !important;} section[data-testid="stSidebar"] {display: none !important;}</style>""", unsafe_allow_html=True)

st.title(f"{PAGE_ICON} Beleg-Flow: DATEV 영수증 AI 파서")

if "parsing_result" not in st.session_state:
    st.session_state.parsing_result = None

# ==============================================================================
# BACKEND ENGINE
# ==============================================================================
def build_datev_filename(date_str, re_index, vendor, brutto_val, currency_str):
    v_clean = _ILLEGAL_CHARS.sub("", vendor).replace(" ", "")[:15]
    d_clean = date_str.replace('-', '').replace('.', '')
    cur = "USD" if str(currency_str).upper() == "USD" else "EUR"
    return f"{re_index}_{d_clean}_{v_clean}_{brutto_val:.2f} {cur}.pdf"

def calculate_tax_details(brutto, mwst_type):
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"): mwst_19 = round(brutto * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only": mwst_7 = round(brutto * MWST_7_FACTOR, 2)
    return mwst_19, mwst_7, round(brutto - (mwst_19 + mwst_7), 2)

# ==============================================================================
# TAB 2: 업로드 및 파싱
# ==============================================================================
# (기존 로직 유지, 데이터 프레임 생성 시 'Interner Beleg-Nr' 컬럼 초기값 ""로 추가)
# ... [중략] ...
# final_rows.append({
#     "고유 번호": re_index_str,
#     "Interner Beleg-Nr": "",  # <--- 추가된 컬럼
#     "Belegdatum": date_str,
#     ...
# })

# ==============================================================================
# TAB 3: 검토 및 내보내기 (에디터 설정)
# ==============================================================================
with st.container():
    if st.session_state.parsing_result is not None:
        # 에디터 설정
        safe_config = {
            "고유 번호": st.column_config.TextColumn("고유 번호", disabled=True),
            "Interner Beleg-Nr": st.column_config.TextColumn("내부 인보이스 번호"), # 입력 가능
            "Bruttobetrag": st.column_config.NumberColumn("Bruttobetrag", format="%.2f"),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("파일명", disabled=True),
            # ... 나머지 컬럼 설정
        }
        
        edited_df = st.data_editor(
            st.session_state.parsing_result,
            column_config=safe_config,
            use_container_width=True
        )
        st.session_state.parsing_result = edited_df

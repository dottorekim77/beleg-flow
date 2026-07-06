import io
import re
import time
import zipfile
from datetime import datetime

import google.generativeai as genai
import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image
from pypdf import PdfReader, PdfWriter
import streamlit as st

# ==============================================================================
# KONSTANTEN & CONFIG
# ==============================================================================
PAGE_TITLE      = "Beleg-Flow & DATEV Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"   
FREE_TIER_DELAY = 4.2                        
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107
MIME_MAP = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte", "Bankeinzug", "Überweisung", "PayPal", "Bar"]
_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.markdown("""<style>[data-testid="stSidebarNav"] {display: none !important;} section[data-testid="stSidebar"] {display: none !important;}</style>""", unsafe_allow_html=True)

if "parsing_result" not in st.session_state: st.session_state.parsing_result = None

# ==============================================================================
# BACKEND & HELPER FUNCTIONS
# ==============================================================================
def build_datev_filename(date_str, re_index, vendor, total_val):
    v_clean = _ILLEGAL_CHARS.sub("", vendor).replace(" ", "")[:15]
    d_clean = date_str.replace('-', '').replace('.', '')
    # 요청하신 규칙: RE-xxx_날짜_업체명_가격.pdf
    return f"{re_index}_{d_clean}_{v_clean}_{total_val:.2f}.pdf"

def calculate_tax_details(brutto_eur, mwst_type):
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"): mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only": mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    return mwst_19, mwst_7, round(brutto_eur - (mwst_19 + mwst_7), 2)

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_clean = df.drop(columns=["_RawBytes", "_OcrText", "_FileExt"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=False)
    return buf.getvalue()

# ==============================================================================
# MAIN UI
# ==============================================================================
tab1, tab2, tab3 = st.tabs(["⚙️ 1. 설정", "📁 2. 업로드 및 AI 파싱", "📊 3. 결과 검토 및 내보내기"])

with tab2:
    uploaded_files = st.file_uploader("영수증 파일 업로드", accept_multiple_files=True)
    if st.button("🚀 파싱 시작"):
        final_rows = []
        for idx, f in enumerate(uploaded_files):
            re_idx = f"RE-{idx+1:03d}"
            # 실제 사용 시 ask_gemini_vision_cached 연동
            total = 99.99 
            final_rows.append({
                "고유 번호": re_idx,
                "내부 인보이스 번호": "", # 사용자 입력 대기
                "날짜": datetime.now().strftime("%Y-%m-%d"),
                "업체명": "TestVendor",
                "금액": f"{total:,.2f} EUR",
                "세금 유형": "AUTO_19",
                "파일명": build_datev_filename(datetime.now().strftime("%Y-%m-%d"), re_idx, "TestVendor", total),
                "_RawBytes": f.read(), "_OcrText": "", "_FileExt": "pdf"
            })
        st.session_state.parsing_result = pd.DataFrame(final_rows)

with tab3:
    if st.session_state.parsing_result is not None:
        safe_config = {
            "고유 번호": st.column_config.TextColumn("고유 번호", disabled=True),
            "내부 인보이스 번호": st.column_config.TextColumn("내부 인보이스 번호"),
            "파일명": st.column_config.TextColumn("파일명", disabled=True),
            "금액": st.column_config.TextColumn("금액", disabled=True),
            "세금 유형": st.column_config.SelectboxColumn("세금 유형", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"]),
        }
        edited_df = st.data_editor(
            st.session_state.parsing_result,
            column_config=safe_config,
            num_rows="fixed",
            height=420,
            use_container_width=True
        )
        st.session_state.parsing_result = edited_df
        
        if st.download_button("📊 엑셀 다운로드", data=build_excel_bytes(edited_df), file_name="export.xlsx"):
            st.success("다운로드 완료")

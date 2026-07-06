import io
import os
import re
import time
import zipfile
from datetime import datetime

import google.generativeai as genai
import numpy as np
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

MIME_MAP = {
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
}

ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte", "Bankeinzug", "Überweisung", "PayPal", "Bar"]

_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

# ==============================================================================
# STREAMLIT PAGE SETUP & CLEAN UI HACKS
# ==============================================================================
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

st.markdown("""
    <style>
        [data-testid="stSidebarNav"] {display: none !important;}
        section[data-testid="stSidebar"] {display: none !important;}
        .block-container {padding-top: 2rem !important; padding-bottom: 2rem !important;}
    </style>
""", unsafe_allow_html=True)

st.title(f"{PAGE_ICON} Beleg-Flow: DATEV 영수증 AI 파서")
st.caption("AI 기반으로 영수증 데이터를 추출하고 지정된 고유 번호 형식(RE-xxx_날짜_업체명_금액)으로 파일명을 자동 변경합니다.")

# Session State 초기화
if "config" not in st.session_state:
    st.session_state.config = {
        "kontenrahmen": "SKR04",
        "default_zahlungsart": "Bankeinzug"
    }
if "parsing_result" not in st.session_state:
    st.session_state.parsing_result = None

# ==============================================================================
# API AUTHENTIFIZIERUNG
# ==============================================================================
API_KEY: str = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    API_KEY = st.text_input("🔑 Gemini API-Key eingeben", type="password")
    if API_KEY: genai.configure(api_key=API_KEY)
else:
    genai.configure(api_key=API_KEY)

# ==============================================================================
# BACKEND ENGINE FUNCTIONS
# ==============================================================================

@st.cache_data(show_spinner=False)
def ask_gemini_vision_cached(file_bytes: bytes, mime_type: str, skr_mode: str, api_key_trigger: str) -> tuple:
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", "AUTO_19", "No OCR text")
    if not api_key_trigger: return fallback
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = get_gemini_prompt(skr_mode)
        response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, prompt_text])
        beleg_nr, d_str, ven, tot, cur, m_type = _parse_gemini_response(response.text)
        return beleg_nr, d_str, ven, tot, cur, m_type, response.text, True
    except Exception:
        return fallback + (False,)

def create_sandwich_pdf(file_bytes: bytes, ext: str, raw_ai_text: str) -> bytes:
    try:
        writer = PdfWriter()
        if ext in ["jpg", "jpeg", "png"]:
            img = Image.open(io.BytesIO(file_bytes))
            img_pdf_buf = io.BytesIO()
            img.convert("RGB").save(img_pdf_buf, format="PDF")
            img_pdf_buf.seek(0)
            reader = PdfReader(img_pdf_buf)
            page = reader.pages[0]
        elif ext == "pdf":
            reader = PdfReader(io.BytesIO(file_bytes))
            page = reader.pages[0]
        else:
            return file_bytes
        writer.add_page(page)
        writer.add_metadata({"/Title": "DATEV Searchable Beleg", "/Subject": raw_ai_text.replace("\n", " ")})
        output_buf = io.BytesIO()
        writer.write(output_buf)
        return output_buf.getvalue()
    except Exception: return file_bytes

def sanitize_filename(text: str) -> str: return _ILLEGAL_CHARS.sub("", text).strip()

# 🎯 요청하신 파일명 형식으로 변경: RE-xxx_날짜_업체명_금액 EUR/USD.pdf
def build_datev_filename(date_str: str, re_index: str, vendor: str, brutto_val: float, currency_str: str) -> str:
    v_clean = sanitize_filename(vendor).replace(" ", "")[:15]
    d_clean = date_str.replace('-', '').replace('.', '')
    cur = "USD" if str(currency_str).upper() == "USD" else "EUR"
    return f"{re_index}_{d_clean}_{v_clean}_{brutto_val:.2f} {cur}.pdf"

def get_gemini_prompt(skr_mode: str) -> str:
    return """Du bist ein Experte für deutsche Finanzbuchhaltung. Extrahiere folgende Daten aus dem Beleg:
1. Rechnungsnummer (Original)
2. Rechnungsdatum (YYYY-MM-DD)
3. Verkäufer (max 15 Zeichen)
4. Bruttobetrag (Zahl mit Punkt .)
5. Währung (EUR/USD)
6. MwSt_Type ("19_Only", "7_Only", "Split", "0_Only", "AUTO_19")

Ausgabe strictly 6 Zeilen:
Beleg_Nr: [Nummer]
Datum: [YYYY-MM-DD]
Vendor: [Name]
Total: [Zahl]
Currency: [EUR/USD]
MwSt_Type: [Type]"""

def _parse_german_amount(raw: str) -> float:
    s = re.sub(r"[€$£\s]", "", raw)
    s = re.sub(r"(?i)(eur|usd|gbp)", "", s).strip()
    if not s: return 0.0
    if "." in s and "," in s:
        if s.rfind(".") > s.rfind(","): s = s.replace(",", "")
        else: s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        if len(s[s.rfind(",") + 1:]) == 2: s = s.replace(",", ".")
        else: s = s.replace(",", "")
    try: return float(s)
    except ValueError: return 0.0

def _parse_gemini_response(text: str) -> tuple:
    beleg_nr, date_str, vendor, total, currency = "", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR"
    mwst_type = "AUTO_19"
    cleaned = re.sub(r"[*`]", "", text)
    for line in cleaned.splitlines():
        if ":" not in line: continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        match key:
            case "Beleg_Nr": beleg_nr = value
            case "Datum":
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): date_str = value
            case "Vendor": 
                if value: vendor = value
            case "Total": total = _parse_german_amount(value)
            case "Currency":
                if value.upper() in ["EUR", "USD"]: currency = value.upper()
            case "MwSt_Type":
                if value: mwst_type = value
    return beleg_nr, date_str, vendor, total, currency, mwst_type

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple[float, float, float]:
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"): mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only": mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif mwst_type == "Split":
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
    return mwst_19, mwst_7, round(brutto_eur - (mwst_19 + mwst_7), 2)

# ==============================================================================
# REKALKULATION & EXPORT
# ==============================================================================

def on_table_edited() -> None:
    edit_state = st.session_state.get("parsing_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})
    if not edited_rows: return

    df = st.session_state.parsing_result.copy()

    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]
        
        for col, new_val in changes.items():
            df.at[label, col] = new_val

        brutto_val = float(df.at[label, "Bruttobetrag"])
        mwst_19, mwst_7, netto = calculate_tax_details(brutto_val, str(df.at[label, "Steuerschlüssel"]))
        df.at[label, "USt/Vorsteuer 19%"] = mwst_19
        df.at[label, "Vorsteuer 7%"] = mwst_7
        df.at[label, "Nettobetrag"] = netto
        
        # 수정 시에도 새로운 RE-xxx 구조 파일명 유지 반영
        df.at[label, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Belegdatum"]), str(df.at[label, "고유 번호"]), 
            str(df.at[label, "Aussteller (Vendor)"]), brutto_val, str(df.at[label, "Währung"])
        )
    st.session_state.parsing_result = df

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=False)
        ws = writer.sheets["DATEV_Export"]
        HEADER_FILL, HEADER_FONT = PatternFill("solid", fgColor="1F4E78"), Font(name="Arial", size=11, bold=True, color="FFFFFF")
        thin = Side(style="thin", color="D9D9D9")
        border_style = Border(left=thin, right=thin, top=thin, bottom=thin)

        for cell in ws[1]: cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, border_style
        for row in ws.iter_rows(min_row=2):
            for col_idx, cell in enumerate(row, start=1):
                cell.border = border_style
                if col_idx in (5, 6, 7, 8): cell.number_format = '#,##0.00'

        for col in ws.columns:
            max_len = 0
            for cell in col:
                if cell.value is not None:
                    str_len = sum(2 if ord(char) > 128 else 1 for char in str(cell.value))
                    if str_len > max_len: max_len = str_len
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 5, 16)
    return buf.getvalue()

# ==============================================================================
# MAIN UI TABS
# ==============================================================================
tab1, tab2, tab3 = st.tabs(["⚙️ 1. 기본 설정", "📁 2. 영수증 업로드 & AI 분석", "📊 3. DATEV 결과 및 내보내기"])

# --- TAB 1: 기본 설정 ---
with tab1:
    st.header("⚙️ 회계 및 시스템 기본 설정")
    selected_skr = st.radio("📋 Standardkontenrahmen (SKR)", options=["SKR03", "SKR04"], index=1, horizontal=True)
    default_zahlart = st.selectbox("💳 Standard-Zahlweg (기본 결제 방식)", options=ZAHLART_OPTIONS, index=2)
    if st.button("💾 설정 저장"):
        st.session_state.config["kontenrahmen"] = selected_skr
        st.session_state.config["default_zahlungsart"] = default_zahlart
        st.success("설정 저장 완료!")

# --- TAB 2: 데이터 업로드 & AI 엔진 ---
with tab2:
    st.header("📁 영수증 파일 업로드")
    uploaded_receipt_files = st.file_uploader("분석할 영수증 파일들을 선택하세요 (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
        
    st.markdown("---")
    
    if st.button("🚀 Gemini AI 영수증 일괄 파싱 시작", type="primary"):
        if not uploaded_receipt_files:
            st.warning("분석할 영수증 파일을 업로드해 주세요.")
            st.stop()
            
        final_rows = []
        total_files = len(uploaded_receipt_files)
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, f in enumerate(uploaded_receipt_files):
            re_index_str = f"RE-{idx+1:03d}"
            
            status_text.text(f"🔮 [{re_index_str}] Gemini AI 분석 중... ({idx+1}/{total_files}): {f.name}")
            file_bytes = f.read()
            ext = f.name.rsplit(".", 1)[-1].lower()
            mime_type = MIME_MAP.get(ext, "application/octet-stream")
            
            res = ask_gemini_vision_cached(file_bytes, mime_type, selected_skr, API_KEY)
            orig_beleg_nr, date_str, vendor, total, currency, mwst_type, raw_text = res[0], res[1], res[2], res[3], res[4], res[5], res[6]
            was_called = res[7] if len(res) > 7 else False
            
            mwst_19, mwst_7, netto = calculate_tax_details(total, mwst_type)
            # 새 규칙 맞춤 파일명 빌드
            datev_fn = build_datev_filename(date_str, re_index_str, vendor, total, currency)
            
            final_rows.append({
                "고유 번호": re_index_str,
                "Belegdatum": date_str,
                "Aussteller (Vendor)": vendor,
                "Original Beleg-Nr": orig_beleg_nr,
                "Bruttobetrag": total,
                "Währung": currency,
                "USt/Vorsteuer 19%": mwst_19,
                "Vorsteuer 7%": mwst_7,
                "Nettobetrag": netto,
                "Zahlweg (DATEV)": default_zahlart,
                "SKR_Konto": "", 
                "Steuerschlüssel": mwst_type,
                "Zukünftiger DATEV-Dateiname": datev_fn,
                "_FileExt": ext, "_RawBytes": file_bytes, "_OcrText": raw_text
            })
            
            progress_bar.progress(int((idx + 1) / total_files * 100))
            if was_called and total_files > 1 and idx < total_files - 1: 
                time.sleep(FREE_TIER_DELAY)
                
        status_text.success("✅ 모든 영수증의 파일명 매핑 및 데이터 추출이 완료되었습니다!")
        
        st.session_state.parsing_result = pd.DataFrame(final_rows)
        st.info("3번째 탭으로 이동하여 변환된 인덱스 번호와 파일명을 확인하세요.")

# --- TAB 3: 최종 검토 및 DATEV 내보내기 ---
with tab3:
    st.header("📊 DATEV AI 파싱 결과 검토")
    
    if st.session_state.parsing_result is None:
        st.info("2번째 탭에서 영수증을 업로드하고 파싱을 시작해 주세요.")
    else:
        df_p = st.session_state.parsing_result
        st.markdown(f"**총 {len(df_p)}건**의 영수증 전표가 로드되었습니다.")
        
        safe_config = {
            "고유 번호": st.column_config.TextColumn("고유 번호", disabled=True),
            "Bruttobetrag": st.column_config.NumberColumn("Bruttobetrag", format="%.2f"),
            "Währung": st.column_config.SelectboxColumn("Währung", options=["EUR", "USD"]),
            "USt/Vorsteuer 19%": st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%.2f"),
            "Vorsteuer 7%": st.column_config.NumberColumn("Vorsteuer 7%", format="%.2f"),
            "Nettobetrag": st.column_config.NumberColumn("Nettobetrag", format="%.2f"),
            "Zahlweg (DATEV)": st.column_config.SelectboxColumn("Zahlweg (DATEV)", options=ZAHLART_OPTIONS),
            "SKR_Konto": st.column_config.TextColumn("SKR_Konto"),
            "Steuerschlüssel": st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"]),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", disabled=True),
            "_FileExt": None, "_RawBytes": None, "_OcrText": None
        }
        
        edited_df = st.data_editor(
            df_p,
            use_container_width=True, 
            num_rows="fixed", 
            height=450, 
            key="parsing_editor_key", 
            on_change=on_table_edited,
            column_config=safe_config
        )
        
        st.markdown("---")
        st.subheader("📥 DATEV 세무 데이터 내보내기")
        col_dl1, col_dl2 = st.columns(2)
        today = datetime.now().strftime("%Y%m%d")
        
        with col_dl1:
            st.download_button(
                label="📊 DATEV 부킹 리스트 Excel 다운로드 (.xlsx)",
                data=build_excel_bytes(edited_df),
                file_name=f"DATEV_BelegList_{today}.xlsx",
                use_container_width=True
            )
        with col_dl2:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for _, row in edited_df.iterrows():
                    if row["_RawBytes"] != b"":
                        sandwich_pdf_bytes = create_sandwich_pdf(row["_RawBytes"], row["_FileExt"], row["_OcrText"])
                        zip_file.writestr(row["Zukünftiger DATEV-Dateiname"], sandwich_pdf_bytes)
            zip_buffer.seek(0)
            
            st.download_button(
                label="📁 지정된 파일명 규칙의 PDF ZIP 다운로드 (.zip)",
                data=zip_buffer.getvalue(),
                file_name=f"DATEV_Anlagen_{today}.zip",
                use_container_width=True,
                type="primary"
            )

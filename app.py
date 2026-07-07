import io
import re
import time
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st
import google.generativeai as genai
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pypdf import PdfReader, PdfWriter
from PIL import Image

# ══════════════════════════════════════════════════════════════════════════════
# KONSTANTEN & CONFIG (유료 요금제 및 보안 최적화)
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DATEV Beleg-Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"        # 유료 최고속 모델 적용
FREE_TIER_DELAY = 0.0                       # 유료이므로 강제 지연 0초로 해제
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107

MIME_MAP = {
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
}

ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte"]
Z_CODE_MAP      = {"Firmenkonto": "BANK", "Kreditkarte": "CC"}

_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

INITIAL_VENDORS = {
    "Adobe":      {"SKR03": "4930 - Bürobedarf", "SKR04": "6815 - Bürobedarf"},
    "Amazon":     {"SKR03": "4980 - Betriebsbedarf", "SKR04": "6300 - Sonstige Aufwendungen"},
    "Google":     {"SKR03": "4930 - Bürobedarf", "SKR04": "6815 - Bürobedarf"},
    "Shell":      {"SKR03": "4530 - Kfz-Betriebskosten", "SKR04": "6520 - Kfz-Betriebskosten"},
    "Aral":       {"SKR03": "4530 - Kfz-Betriebskosten", "SKR04": "6520 - Kfz-Betriebskosten"},
    "Telekom":    {"SKR03": "4920 - Telefon", "SKR04": "6805 - Telefon"},
    "Ionq":       {"SKR03": "4980 - Betriebsbedarf", "SKR04": "6300 - Sonstige Aufwendungen"},
}

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

# ══════════════════════════════════════════════════════════════════════════════
# API AUTHENTIFIZIERUNG
# ══════════════════════════════════════════════════════════════════════════════
API_KEY = st.secrets.get("GEMINI_API_KEY", "")

if not API_KEY:
    API_KEY = st.sidebar.text_input("🔑 Gemini API-Key", type="password")
    if not API_KEY:
        st.warning("⚠️ Bitte GEMINI_API_KEY eingeben 또는 Streamlit Secrets에 등록하세요.")
        st.stop()

genai.configure(api_key=API_KEY)

# 세션 상태 초기화
if "custom_rules" not in st.session_state:
    st.session_state.custom_rules = INITIAL_VENDORS.copy()
if "edited_receipts" not in st.session_state:
    st.session_state.edited_receipts = None

# ══════════════════════════════════════════════════════════════════════════════
# GERMAN NUMBER FORMATTER
# ══════════════════════════════════════════════════════════════════════════════
def to_german_amount_str(val: float) -> str:
    try:
        us_style = f"{float(val):,.2f}"
        placed = us_style.replace(",", "PLACEHOLDER")
        placed = placed.replace(".", ",")
        german_style = placed.replace("PLACEHOLDER", ".")
        return german_style
    except (ValueError, TypeError):
        return "0,00"

# ══════════════════════════════════════════════════════════════════════════════
# BACKEND ENGINE (보안을 위해 @st.cache_data 데코레이터를 완전히 제거함)
# ══════════════════════════════════════════════════════════════════════════════
def sanitize_filename(text: str) -> str:
    return _ILLEGAL_CHARS.sub("", text).strip()

def build_datev_filename(date_str: str, vendor: str, brutto_eur: float, ausgang_inv: str) -> str:
    d_clean = date_str.replace('-', '')
    v_clean = sanitize_filename(vendor).replace(" ", "")[:12]
    p_part  = f"{to_german_amount_str(brutto_eur)}EUR"
    base_name = f"{d_clean}_{v_clean}_{p_part}"
    
    if ausgang_inv and str(ausgang_inv).strip() and str(ausgang_inv).lower() != "none":
        inv_part = f"_INV-{sanitize_filename(str(ausgang_inv))}"
        return f"{base_name}{inv_part}.pdf"
    
    return f"{base_name}.pdf"

def ask_gemini_vision_direct(file_bytes: bytes, mime_type: str, skr_mode: str) -> tuple:
    """기록 유출 방지를 위해 캐시 없이 매번 순수하게 호출하고 소멸하는 함수"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = get_gemini_prompt(skr_mode)
        
        # 유료 서버의 끝까지 대기하도록 제한시간 제한 해제
        response = model.generate_content(
            [{"mime_type": mime_type, "data": file_bytes}, prompt_text]
        )
        
        if response.text and "Total" in response.text:
            return _parse_gemini_response(response.text) + (response.text,)
    except Exception as e:
        st.error(f"API Error: {e}")
        
    return ("", datetime.now().strftime("%Y-%m-%d"), "Fehler/Timeout", 0.0, "EUR", "", "AUTO_19", "No OCR text")

def get_assigned_account(vendor_name: str, skr_mode: str) -> str:
    v_upper = vendor_name.upper()
    for keyword, accounts in st.session_state.custom_rules.items():
        if keyword.upper() in v_upper:
            return accounts[skr_mode]
    return ""

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
    except Exception:
        return file_bytes

def get_gemini_prompt(skr_mode: str) -> str:
    return """Du bist ein Rechnungs-Parser. Extrahiere strictly diese 7 Zeilen:
Beleg_Nr: [Rechnungsnummer]
Datum: [YYYY-MM-DD]
Vendor: [Verkäufer max 12 글자]
Total: [Bruttobetrag Zahl mit .]
Currency: [EUR/USD]
Kategorie: AUTO
MwSt_Type: [19_Only/7_Only/Split/0_Only/AUTO_19]"""

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
    kategorie, mwst_type = "", "AUTO_19"
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
    return beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple[float, float, float]:
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"): mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only": mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif mwst_type == "Split":
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
    return mwst_19, mwst_7, round(brutto_eur - (mwst_19 + mwst_7), 2)

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=True)
        ws = writer.sheets["DATEV_Export"]
        HEADER_FILL, HEADER_FONT = PatternFill("solid", fgColor="1F4E78"), Font(name="Arial", size=11, bold=True, color="FFFFFF")
        thin = Side(style="thin", color="D9D9D9")
        border_style = Border(left=thin, right=thin, top=thin, bottom=thin)

        for cell in ws[1]: cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, border_style
        for row in ws.iter_rows(min_row=2):
            for col_idx, cell in enumerate(row, start=1):
                cell.border = border_style
                if col_idx in (6, 7, 8, 9): cell.number_format = '#.##0,00" €"'
                elif col_idx in (1, 5): cell.alignment = Alignment(horizontal="right")

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN UI RENDERING
# ══════════════════════════════════════════════════════════════════════════════
st.title(f"{PAGE_ICON} {PAGE_TITLE}")
st.caption("🔒 본 프로그램은 영수증 기록을 파일이나 서버 캐시에 남기지 않는 완전 휘발성 보안 모드로 동작합니다.")

with st.expander("📝 Buchungsregeln verwalten", expanded=False):
    with st.form("new_rule_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 3, 3])
        with c1: new_vendor = st.text_input("Vendor")
        with c2: new_skr03  = st.text_input("SKR03")
        with c3: new_skr04  = st.text_input("SKR04")
        if st.form_submit_button("💾 Regel speichern") and new_vendor:
            st.session_state.custom_rules[new_vendor] = {"SKR03": new_skr03, "SKR04": new_skr04}
            st.rerun()

    if st.session_state.custom_rules:
        for v in list(st.session_state.custom_rules.keys()):
            r_col1, r_col2, r_col3, r_col4 = st.columns([2, 3, 3, 1])
            r_col1.text(v)
            r_col2.text(st.session_state.custom_rules[v]["SKR03"])
            r_col3.text(st.session_state.custom_rules[v]["SKR04"])
            if r_col4.button("❌", key=f"del_{v}"):
                del st.session_state.custom_rules[v]
                st.rerun()

st.markdown("---")

uploaded_files = st.file_uploader("📂 Digitale Belege hochladen", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1: default_zahlart = st.radio("💳 Standard-Zahlweg", options=ZAHLART_OPTIONS, horizontal=True)
with col_cfg2: selected_skr = st.radio("📋 Standardkontenrahmen", options=["SKR03", "SKR04"], horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key = batch_key
        st.session_state.edited_receipts = None

    if st.session_state.edited_receipts is None:
        rows = []
        progress_bar = st.progress(0.0)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            file_bytes = uploaded_file.read()
            ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
            mime_type  = MIME_MAP.get(ext, "application/octet-stream")

            # 🔒 대기 시간 없이, 캐시 없이 다이렉트로 구글 AI에 전송
            res = ask_gemini_vision_direct(file_bytes, mime_type, selected_skr)
            beleg_nr, date_str, vendor, total, currency, _, mwst_type, raw_text = res

            assigned_kategorie = get_assigned_account(vendor, selected_skr)
            mwst_19, mwst_7, netto = calculate_tax_details(total, mwst_type)

            rows.append({
                "Rechnungsdatum": date_str, "Verkäufer": vendor, "Beleg_Nr": beleg_nr,
                "Beleg-Soll (Orig.)": f"{to_german_amount_str(total)} {currency}", "Bruttobetrag (EUR)": total,
                "Zahlweg (DATEV)": default_zahlart, f"{selected_skr}": assigned_kategorie,
                "USt/Vorsteuer 19%": mwst_19, "Vorsteuer 7%": mwst_7, "Nettobetrag (Haben)": netto,
                "Steuerschlüssel": mwst_type, "🔗 Ausgangs-INV": "",
                "Zukünftiger DATEV-Dateiname": build_datev_filename(date_str, vendor, total, ""),
                "_FileExt": ext, "_RawBytes": file_bytes, "_OcrText": raw_text
            })
            progress_bar.progress((idx + 1) / len(uploaded_files))
            if FREE_TIER_DELAY > 0: time.sleep(FREE_TIER_DELAY)

        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # 데이터 수동 편집 화면
    df_input = st.session_state.edited_receipts
    edited_df = st.data_editor(
        df_input, use_container_width=True, num_rows="fixed",
        column_config={
            "Rechnungsdatum": st.column_config.TextColumn("📅 Datum"),
            "Verkäufer": st.column_config.TextColumn("Vendor"),
            "Beleg_Nr": st.column_config.TextColumn("Beleg_Nr"),
            "Bruttobetrag (EUR)": st.column_config.NumberColumn("Brutto (EUR)", format="%.2f €"),
            "Steuerschlüssel": st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"]),
            "_FileExt": None, "_RawBytes": None, "_OcrText": None
        }
    )

    # 수정사항 실시간 동기화 및 파일명 재생성
    if not edited_df.equals(df_input):
        for idx in edited_df.index:
            b_eur = float(edited_df.at[idx, "Bruttobetrag (EUR)"])
            m_19, m_7, net = calculate_tax_details(b_eur, str(edited_df.at[idx, "Steuerschlüssel"]))
            edited_df.at[idx, "USt/Vorsteuer 19%"] = m_19
            edited_df.at[idx, "Vorsteuer 7%"] = m_7
            edited_df.at[idx, "Nettobetrag (Haben)"] = net
            edited_df.at[idx, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
                str(edited_df.at[idx, "Rechnungsdatum"]), str(edited_df.at[idx, "Verkäufer"]), b_eur, str(edited_df.at[idx, "🔗 Ausgangs-INV"])
            )
        st.session_state.edited_receipts = edited_df
        st.rerun()

    # 파일 다운로드 버튼 구성
    df_final = st.session_state.edited_receipts
    today = datetime.now().strftime("%Y%m%d")
    
    st.markdown("### 📥 Bereitstellung der DATEV-Exportdateien")
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1: 
        st.download_button(label="📊 Buchungsliste herunterladen (.xlsx)", data=build_excel_bytes(df_final), file_name=f"DATEV_Buchungsliste_{today}.xlsx", use_container_width=True)
    with col_dl2:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for _, row in df_final.iterrows():
                sandwich_pdf_bytes = create_sandwich_pdf(row["_RawBytes"], row["_FileExt"], row["_OcrText"])
                zip_file.writestr(row["Zukünftiger DATEV-Dateiname"], sandwich_pdf_bytes)
        st.download_button(label="📁 PDF-Belege herunterladen (.zip)", data=zip_buffer.getvalue(), file_name=f"DATEV_Belege_{today}.zip", use_container_width=True, type="primary")

    # ══════════════════════════════════════════════════════════════════════════════
    # 🔒 강력한 데이터 파기 버튼 (클릭 즉시 메모리 완전 증발)
    # ══════════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    if st.button("🚨 모든 작업 기록 및 영수증 데이터 완전히 파기하기", use_container_width=True, type="secondary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.toast("메모리에 있던 모든 영수증 흔적이 영구적으로 파기되었습니다.", icon="🔒")
        time.sleep(0.6)
        st.rerun()

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
# CONFIG & HARD RESET
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DATEV Beleg-Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"   
FREE_TIER_DELAY = 4.0                       
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107
ITEMS_PER_PAGE  = 10  

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

# ══════════════════════════════════════════════════════════════════════════════
# API AUTHENTIFIZIERUNG (인증 및 안전장치 강화)
# ══════════════════════════════════════════════════════════════════════════════
# ⚠️ 주의: 아래 큰따옴표 안에 본인의 실제 Gemini API Key를 반드시 붙여넣으세요!
API_KEY = "여기에_실제_Gemini_API_키를_넣으세요" 

if not API_KEY or API_KEY == "AQ.Ab8RN6IC-RyeZOrWpKfHT913EIz81B5036YoPwqekH6Qr_0YQA":
    API_KEY = st.secrets.get("GEMINI_API_KEY", "")
    if not API_KEY:
        API_KEY = st.text_input("🔑 Gemini API-Key eingeben", type="password", key="main_api_key_input")
        if not API_KEY:
            st.error("🚨 API Key가 유효하지 않거나 입력되지 않았습니다. 코드 내부의 API_KEY 변수에 직접 입력해 주세요.")
            st.stop()

genai.configure(api_key=API_KEY)

# ══════════════════════════════════════════════════════════════════════════════
# APP INITIALIZATION & PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

st.markdown("""
    <style>
        [data-testid="stSidebarNav"] {display: none !important;}
        section[data-testid="stSidebar"] {display: none !important;}
        .block-container {padding-top: 2rem !important; padding-bottom: 2rem !important;}
    </style>
""", unsafe_allow_html=True)

st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v8.0 - 무한로딩 방지)")
st.caption("API 호출 실패 시 무한 대기에 빠지지 않고 화면에 즉시 에러 원인을 노출하도록 개선된 버전입니다.")

# ══════════════════════════════════════════════════════════════════════════════
# CACHE RESET & SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════
if st.button("🔄 시스템 캐시 및 메모리 강제 초기화 (먹통 해결용)"):
    st.cache_data.clear()
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.toast("모든 캐시와 세션이 초기화되었습니다. 파일을 다시 올려주세요!", icon="🧹")
    time.sleep(1)
    st.rerun()

if "custom_rules" not in st.session_state:
    st.session_state.custom_rules = INITIAL_VENDORS.copy()
if "edited_receipts" not in st.session_state:
    st.session_state.edited_receipts = None
if "current_page" not in st.session_state:
    st.session_state.current_page = 0

# ══════════════════════════════════════════════════════════════════════════════
# HELPER: GERMAN NUMBER FORMATTER
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
# BACKEND ENGINE & FILENAME BUILDER
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
    """무한 대기를 방지하기 위해 예외 발생 시 에러 내용을 함께 반환합니다."""
    fallback_date = datetime.now().strftime("%Y-%m-%d")
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = get_gemini_prompt(skr_mode)
        
        response = model.generate_content(
            [{"mime_type": mime_type, "data": file_bytes}, prompt_text],
            request_options={"timeout": 15.0} # 타임아웃 명시적 지정
        )
        
        beleg_nr, d_str, ven, tot, cur, kat, m_type = _parse_gemini_response(response.text)
        return beleg_nr, d_str, ven, tot, cur, kat, m_type, response.text, None
        
    except Exception as e:
        # 에러 정보를 화면단으로 토스하여 무한 대기를 끊음
        error_msg = str(e)
        return ("", fallback_date, "API Error", 0.0, "EUR", "", "AUTO_19", f"Error Detail: {error_msg}", error_msg)

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
    except Exception: return file_bytes

def get_gemini_prompt(skr_mode: str) -> str:
    return """Du bist ein Experte für deutsche Finanzbuchhaltung. Extrahiere folgende Daten:
1. Rechnungsnummer
2. Rechnungsdatum (YYYY-MM-DD)
3. Verkäufer (max 12 Zeichen)
4. Bruttobetrag (Zahl mit Punkt .)
5. Währung (EUR/USD)
6. Kategorie_SKR (Ignoriere dies, gib einfach "AUTO" an)
7. MwSt_Type ("19_Only", "7_Only", "Split", "0_Only", "AUTO_19")

Ausgabe strictly 7 Zeilen:
Beleg_Nr: [Nummer]
Datum: [YYYY-MM-DD]
Vendor: [Name]
Total: [Zahl]
Currency: [EUR/USD]
Kategorie: AUTO
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

# ══════════════════════════════════════════════════════════════════════════════
# REKALKULATION & EXPORT
# ══════════════════════════════════════════════════════════════════════════════
def on_table_edited() -> None:
    edit_state  = st.session_state.get("beleg_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})
    if not edited_rows: return

    df = st.session_state.edited_receipts.copy()
    page = st.session_state.current_page
    
    for row_idx_str, changes in edited_rows.items():
        local_idx = int(row_idx_str)
        global_idx = df.index[page * ITEMS_PER_PAGE + local_idx]
        
        for col, new_val in changes.items(): 
            df.at[global_idx, col] = new_val

        if "Is_Kreditkarte" in changes:
            df.at[global_idx, "Zahlweg (DATEV)"] = "Kreditkarte" if changes["Is_Kreditkarte"] else "Firmenkonto"

        brutto_eur = float(df.at[global_idx, "Bruttobetrag (EUR)"])
        mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, str(df.at[global_idx, "Steuerschlüssel"]))
        df.at[global_idx, "USt/Vorsteuer 19%"] = mwst_19
        df.at[global_idx, "Vorsteuer 7%"]  = mwst_7
        df.at[global_idx, "Nettobetrag (Haben)"]    = netto
        
        df.at[global_idx, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[global_idx, "Rechnungsdatum"]), 
            str(df.at[global_idx, "Verkäufer"]), 
            brutto_eur,
            str(df.at[global_idx, "🔗 Ausgangs-INV"])
        )
    st.session_state.edited_receipts = df

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText", "Is_Kreditkarte"], errors="ignore")
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
            max_len = 0
            for cell in col:
                if cell.value is not None:
                    str_len = sum(2 if ord(char) > 128 else 1 for char in str(cell.value))
                    if str_len > max_len: max_len = str_len
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 5, 16)
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN UI
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("📝 Buchungsregeln verwalten", expanded=False):
    st.caption("Verwalten Sie hier Ihre automatischen Zuweisungsregeln für bekannte Kreditoren.")
    
    with st.form("new_rule_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 3, 3])
        with c1: new_vendor = st.text_input("Vendor", placeholder="z.B. Apple")
        with c2: new_skr03  = st.text_input("SKR03", placeholder="z.B. 4930")
        with c3: new_skr04  = st.text_input("SKR04", placeholder="z.B. 6815")
        
        submit_rule = st.form_submit_button("💾 Regel speichern")
        if submit_rule and new_vendor:
            st.session_state.custom_rules[new_vendor] = {"SKR03": new_skr03, "SKR04": new_skr04}
            st.toast(f"💾 Regel für '{new_vendor}' erfolgreich gespeichert!")

    if st.session_state.custom_rules:
        st.markdown("**Aktuelle Regeln:**")
        for v in list(st.session_state.custom_rules.keys()):
            r_col1, r_col2, r_col3, r_col4 = st.columns([2, 3, 3, 1])
            with r_col1: st.text(v)
            with r_col2: st.text(st.session_state.custom_rules[v]["SKR03"])
            with r_col3: st.text(st.session_state.custom_rules[v]["SKR04"])
            with r_col4: 
                if st.button("❌ Löschen", key=f"del_{v}", use_container_width=True):
                    del st.session_state.custom_rules[v]
                    st.rerun()

st.markdown("---")

uploaded_files = st.file_uploader("📂 Digitale Belege hochladen (PDF, PNG, JPG, JPEG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1: default_zahlart = st.radio("💳 Standard-Zahlweg (DATEV)", options=ZAHLART_OPTIONS, index=0, horizontal=True)
with col_cfg2: selected_skr = st.radio("📋 Standardkontenrahmen (SKR)", options=["SKR03", "SKR04"], index=1, horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key = batch_key
        st.session_state.edited_receipts = None
        st.session_state.current_page = 0

    if st.session_state.edited_receipts is None:
        rows = []
        total_files = len(uploaded_files)
        
        with st.status("🚀 Document Processing Engine Initializing...", expanded=True) as status:
            for idx, uploaded_file in enumerate(uploaded_files):
                current_row_no = idx + 1
                status.update(label=f"🔄 Processing ({current_row_no}/{total_files}): {uploaded_file.name}", state="running")

                file_bytes = uploaded_file.read()
                ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                # 수정한 파싱 함수 호출 (마지막 항목에 에러 유무 확인)
                beleg_nr, date_str, vendor, total, currency, _, mwst_type, raw_text, err = ask_gemini_vision_direct(file_bytes, mime_type, selected_skr)
                
                # 만약 백엔드 호출 도중 에러가 발견되면 상태창에 표시하고 진행 중단
                if err is not None:
                    status.update(label="❌ API Authentication or Quota Error Occurred!", state="error")
                    st.error(f"⚠️ **Gemini API 호출에 실패했습니다.**\n\n**원인 코드:** `{err}`\n\n**해결법:** 코드 내부 46번째 줄의 API_KEY 문자열 값이 사용 가능한 정상적인 키인지 다시 한번 검증해 주세요.")
                    st.stop()

                assigned_kategorie = get_assigned_account(vendor, selected_skr)
                mwst_19, mwst_7, netto = calculate_tax_details(total, mwst_type)
                is_cc_initial = (default_zahlart == "Kreditkarte")

                generated_filename = build_datev_filename(date_str, vendor, total, "")

                rows.append({
                    "Rechnungsdatum":  date_str,                  
                    "🔗 Ausgangs-INV":  "",                         
                    "Verkäufer":        vendor,                     
                    "Beleg_Nr":        beleg_nr,                   
                    "Beleg-Soll (Orig.)": f"{to_german_amount_str(total)} $" if currency == "USD" else f"{to_german_amount_str(total)} €", 
                    "Bruttobetrag (EUR)": total,                    
                    "Is_Kreditkarte":   is_cc_initial,              
                    "Zahlweg (DATEV)":          default_zahlart,    
                    f"{selected_skr}": assigned_kategorie, 
                    "USt/Vorsteuer 19%":  mwst_19,
                    "Vorsteuer 7%":   mwst_7,
                    "Nettobetrag (Haben)":      netto,
                    "Steuerschlüssel":        mwst_type,
                    "Zukünftiger DATEV-Dateiname": generated_filename,
                    "_FileExt": ext, "_RawBytes": file_bytes, "_OcrText": raw_text
                })
                
                if total_files > 1 and idx < total_files - 1: 
                    time.sleep(FREE_TIER_DELAY)
            
            status.update(label="✅ All documents processed successfully!", state="complete")

        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # 🔄 DATA EDITOR WITH PAGINATION
    @st.fragment
    def render_isolated_data_editor():
        df = st.session_state.edited_receipts
        total_rows = len(df)
        max_pages = (total_rows - 1) // ITEMS_PER_PAGE + 1
        
        if st.session_state.current_page >= max_pages:
            st.session_state.current_page = 0
            
        page = st.session_state.current_page
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        
        df_page = df.iloc[start_idx:end_idx]

        st.markdown(f"**📋 Belege bearbeiten (Seite {page + 1} von {max_pages} — Gesamt: {total_rows} Einträge)**")

        st.data_editor(
            df_page,
            use_container_width=True, 
            num_rows="fixed", 
            key="beleg_editor_key", 
            on_change=on_table_edited,
            column_config={
                "Rechnungsdatum":  st.column_config.TextColumn("📅 Rechnungsdatum", width="small"),
                "🔗 Ausgangs-INV":  st.column_config.TextColumn("🔗 Ausgangs-INV (우리회사 인보이스)", width="medium"),
                "Verkäufer":        st.column_config.TextColumn("Verkäufer", width="medium"),
                "Beleg_Nr":        st.column_config.TextColumn("Beleg_Nr (구매영수증번호)", width="medium"),
                "Beleg-Soll (Orig.)":    st.column_config.TextColumn("Beleg-Soll (Orig.)", disabled=True, width="small"), 
                "Bruttobetrag (EUR)":    st.column_config.NumberColumn("Bruttobetrag (EUR)", format="%.2f €", width="small"),
                "Is_Kreditkarte":  st.column_config.CheckboxColumn("💳 CC"),
                "Zahlweg (DATEV)":         st.column_config.TextColumn("Zahlweg (DATEV)", disabled=True, width="small"),
                f"{selected_skr}": st.column_config.TextColumn(f"📊 {selected_skr}", width="medium"),
                "USt/Vorsteuer 19%":  st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%.2f €"),
                "Vorsteuer 7%":   st.column_config.NumberColumn("Vorsteuer 7%", format="%.2f €"),
                "Nettobetrag (Haben)":     st.column_config.NumberColumn("Nettobetrag (Haben)", format="%.2f €"),
                "Steuerschlüssel":       st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"], width="small"),
                "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", width="max"),
                "_FileExt": None, "_RawBytes": None, "_OcrText": None
            },
        )
        
        p_col1, p_col2, p_col3 = st.columns([1, 4, 1])
        with p_col1:
            if st.button("⬅️ Vorherige", disabled=(page == 0), use_container_width=True):
                st.session_state.current_page -= 1
                st.rerun()
        with p_col2:
            st.markdown(f"<p style='text-align: center; color: gray; margin-top: 6px;'>Zeige Einträge {start_idx + 1} bis {min(end_idx, total_rows)}</p>", unsafe_allow_html=True)
        with p_col3:
            if st.button("Nächste ➡️", disabled=(page >= max_pages - 1), use_container_width=True):
                st.session_state.current_page += 1
                st.rerun()

    render_isolated_data_editor()

    # DOWNLOADS
    df_final = st.session_state.edited_receipts
    today = datetime.now().strftime("%Y%m%d")
    st.markdown("### 📥 Bereitstellung der DATEV-Exportdateien")
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1: st.download_button(label=f"📊 Buchungsliste als Excel-Export herunterladen (.xlsx)", data=build_excel_bytes(df_final), file_name=f"DATEV_{selected_skr}_Buchungsliste_{today}.xlsx", use_container_width=True)
    with col_dl2:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for _, row in df_final.iterrows():
                sandwich_pdf_bytes = create_sandwich_pdf(row["_RawBytes"], row["_FileExt"], row["_OcrText"])
                zip_file.writestr(row["Zukünftiger DATEV-Dateiname"], sandwich_pdf_bytes)
        zip_buffer.seek(0)
        st.download_button(label="📁 PDF-Belege als ZIP-Archiv herunterladen (.zip)", data=zip_buffer.getvalue(), file_name=f"DATEV_Digitale_Belege_{today}.zip", use_container_width=True, type="primary")

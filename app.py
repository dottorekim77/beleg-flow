import io
import re
import time
import zipfile
import os
from datetime import datetime

import pandas as pd
import streamlit as st
import google.generativeai as genai
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pypdf import PdfReader, PdfWriter
from PIL import Image

# ══════════════════════════════════════════════════════════════════════════════
# KONSTANTEN & CONFIG
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DATEV Beleg-Parser Pro AI"
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

ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte"]
Z_CODE_MAP      = {"Firmenkonto": "BANK", "Kreditkarte": "CC"}

_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

# [신규 추가] 수동 매칭 데이터 마스터 파일 경로
MAPPING_FILE = "user_mapping.csv"

# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT PAGE SETUP
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v4.0 - Hybrid Framework)")
st.caption("Automatisierte Belegerfassung mit Sandwich-PDF-Generierung, SKR-Klassifizierung und erweiterten Benutzerregeln.")

# ══════════════════════════════════════════════════════════════════════════════
# API AUTHENTIFIZIERUNG
# ══════════════════════════════════════════════════════════════════════════════
API_KEY: str = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    API_KEY = st.text_input("🔑 Gemini API-Key eingeben", type="password")
    if API_KEY:
        genai.configure(api_key=API_KEY)
else:
    genai.configure(api_key=API_KEY)

# ══════════════════════════════════════════════════════════════════════════════
# [신규 추가] 사용자 매칭 규칙 데이터베이스 IO 핸들러
# ══════════════════════════════════════════════════════════════════════════════
def load_mapping():
    if os.path.exists(MAPPING_FILE):
        return pd.read_csv(MAPPING_FILE)
    return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "SKR03_코드", "계정과목명"])

def save_mapping(df):
    df.to_csv(MAPPING_FILE, index=False, encoding='utf-8-sig')

# 시스템 자체 내장형 기본 추천 풀 (단골 지정이 없을 때 작동)
SYSTEM_RECOMMENDATIONS = {
    "wasser": {"SKR04": {"code": "6643", "name": "Aufmerksamkeiten"}, "SKR03": {"code": "4653", "name": "Aufmerksamkeiten"}},
    "keks": {"SKR04": {"code": "6643", "name": "Aufmerksamkeiten"}, "SKR03": {"code": "4653", "name": "Aufmerksamkeiten"}},
    "mail": {"SKR04": {"code": "6830", "name": "EDV-Aufwendungen"}, "SKR03": {"code": "4925", "name": "Telekommunikation/EDV"}},
    "google": {"SKR04": {"code": "6830", "name": "EDV-Aufwendungen"}, "SKR03": {"code": "4925", "name": "Telekommunikation/EDV"}},
    "reparatur": {"SKR04": {"code": "6335", "name": "Instandhaltung"}, "SKR03": {"code": "4260", "name": "Instandhaltung"}},
    "dpd": {"SKR04": {"code": "4730", "name": "Ausgangsfrachten"}, "SKR03": {"code": "4730", "name": "Ausgangsfrachten"}},
    "dhl": {"SKR04": {"code": "4730", "name": "Ausgangsfrachten"}, "SKR03": {"code": "4730", "name": "Ausgangsfrachten"}},
    "steuerberater": {"SKR04": {"code": "7210", "name": "Buchführungskosten"}, "SKR03": {"code": "4955", "name": "Buchführungskosten"}},
    "post": {"SKR04": {"code": "7100", "name": "Porto"}, "SKR03": {"code": "4910", "name": "Porto"}}
}

# ══════════════════════════════════════════════════════════════════════════════
# BACKEND ENGINE FUNCTIONS (CACHED)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def ask_gemini_vision_cached(file_bytes: bytes, mime_type: str, skr_mode: str, api_key_trigger: str) -> tuple:
    default_cat = "4980 - Betriebsbedarf" if skr_mode == "SKR03" else "6300 - Sonstige Aufwendungen"
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", default_cat, "AUTO_19", "No OCR text")
    
    if not api_key_trigger: 
        return fallback
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = get_gemini_prompt(skr_mode)
        response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, prompt_text])
        
        beleg_nr, d_str, ven, tot, cur, kat, m_type = _parse_gemini_response(response.text, default_cat)
        return beleg_nr, d_str, ven, tot, cur, kat, m_type, response.text, True
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
        writer.add_metadata({
            "/Title": "DATEV Searchable Beleg via AI",
            "/Subject": raw_ai_text.replace("\n", " "),
            "/Keywords": "DATEV, OCR, SandwichPDF, Searchable"
        })
        output_buf = io.BytesIO()
        writer.write(output_buf)
        return output_buf.getvalue()
    except Exception:
        return file_bytes

def sanitize_filename(text: str) -> str:
    return _ILLEGAL_CHARS.sub("", text).strip()

def build_datev_filename(
    date_str: str, vendor: str, brutto_eur: float, zahlart: str, beleg_nr: str, inv_nr: str
) -> str:
    z_code = "B" if Z_CODE_MAP.get(zahlart, "BANK") == "BANK" else "C"
    v_clean = sanitize_filename(vendor).replace(" ", "")[:10]
    
    b_suffix = f"_{sanitize_filename(beleg_nr)[:12]}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"-I{sanitize_filename(inv_nr)[:8]}" if inv_nr and inv_nr.lower() not in ("", "none") else ""
    
    date_compact = date_str.replace("-", "")
    return f"{date_compact}_{v_clean}_{brutto_eur:.2f}EUR_{z_code}{b_suffix}{inv_suffix}.pdf"

def get_gemini_prompt(skr_mode: str) -> str:
    if skr_mode == "SKR03":
        skr_guide = "\n   - 3400 (Wareneinkauf)\n   - 8120 (Steuerfreie Umsätze)\n   - 4930 (Bürobedarf)\n   - 4980 (Betriebsbedarf)\n   - 4530 (Kfz-Betriebskosten)\n   - 4660 (Reisekosten)\n   - 4400 (Gebühren)"
    else:
        skr_guide = "\n   - 5400 (Wareneinkauf)\n   - 4120 (Steuerfreie Umsätze)\n   - 6815 (Bürobedarf)\n   - 6300 (Sonstige Aufwendungen)\n   - 6520 (Kfz-Betriebskosten)\n   - 6650 (Reisekosten)\n   - 6855 (Gebühren)"

    return f"""Du bist ein Experte für deutsche Finanzbuchhaltung nach DATEV-Standard. Extrahiere folgende Daten:
1. Rechnungsnummer
2. Rechnungsdatum (YYYY-MM-DD)
3. Verkäufer (max 12 Zeichen)
4. Bruttobetrag (Zahl mit Punkt .)
5. Währung (EUR/USD)
6. Kategorie_SKR ({skr_mode} Code - Bezeichnung aus:{skr_guide})
7. MwSt_Type ("19_Only", "7_Only", "Split", "0_Only", "AUTO_19")

Ausgabe strictly 7 Zeilen:
Beleg_Nr: [Nummer]
Datum: [YYYY-MM-DD]
Vendor: [Name]
Total: [Zahl]
Currency: [EUR/USD]
Kategorie: [Code - Bezeichnung]
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

def _parse_gemini_response(text: str, default_cat: str) -> tuple:
    beleg_nr, date_str, vendor, total, currency = "", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR"
    kategorie, mwst_type = default_cat, "AUTO_19"
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
            case "Kategorie":
                if value: kategorie = value
            case "MwSt_Type":
                if value: mwst_type = value
    return beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple[float, float, float]:
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"):
        mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only":
        mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif mwst_type == "Split":
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
        
    netto = round(brutto_eur - (mwst_19 + mwst_7), 2)
    return mwst_19, mwst_7, netto

# [신규 추가] 가독성 향상 포맷팅 보정 파이프라인 엔진
def assign_readability_and_rules(vendor, date_val, inv_val, skr_mode):
    mapping_df = load_mapping()
    v_str = str(vendor).strip() if not pd.isna(vendor) else "Unbekannt"
    v_lower = v_str.lower()
    
    # 1. 날짜 처리 보정
    d_str = str(date_val).strip() if not pd.isna(date_val) else ""
    if len(d_str) == 8 and d_str.isdigit():
        d_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
        
    # 2. 인보이스 코드 교정
    i_str = str(inv_val).strip() if not pd.isna(inv_val) else ""
    if i_str.startswith('I') and not i_str.startswith('INV-'):
        i_str = f"INV-{i_str[1:]}"

    # 3. 단골 거래처 계정과목 우선 매칭
    target_col = "SKR04_코드" if skr_mode == "SKR04" else "SKR03_코드"
    
    # 1순위: 사용자 지정 영구 매칭 테이블 우선 조회 (콤마 분할 스캔 업그레이드)
    for _, row in mapping_df.iterrows():
        raw_keyword = str(row['판매처_키워드']) if not pd.isna(row['판매처_키워드']) else ""
        
        # 콤마(,)로 구분된 단어들을 쪼개서 리스트로 만듦 (공백 제거 및 소문자화)
        keywords = [k.strip().lower() for k in raw_keyword.split(',') if k.strip()]
        
        # 쪼갠 키워드 중 하나라도 영수증 판매처명에 포함되어 있는지 확인
        if any(k in v_lower for k in keywords):
            code = str(row[target_col]) if not pd.isna(row[target_col]) and str(row[target_col]).strip() else "9999"
            name = str(row['계정과목명']) if not pd.isna(row['계정과목명']) else "Custom Rule"
            return d_str, i_str, f"{code} - {name}"
            
    # 2순위: 내장 시스템 추천 규칙 풀 매칭
    for key, data in SYSTEM_RECOMMENDATIONS.items():[cite: 2]
        if key in v_lower:[cite: 2]
            return d_str, i_str, f"{data[skr_mode]['code']} - {data[skr_mode]['name']} (추천)"[cite: 2]
            
    return d_str, i_str, None
# ══════════════════════════════════════════════════════════════════════════════
# REKALKULATION BEI MANUELLER ÄNDERUNG
# ══════════════════════════════════════════════════════════════════════════════

def on_table_edited() -> None:
    edit_state  = st.session_state.get("beleg_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})
    if not edited_rows: return

    df = st.session_state.edited_receipts.copy()

    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]
        for col, new_val in changes.items():
            df.at[label, col] = new_val

        if "Is_Kreditkarte" in changes:
            is_cc = changes["Is_Kreditkarte"]
            df.at[label, "Zahlart (DATEV)"] = "Kreditkarte" if is_cc else "Firmenkonto"

        brutto_eur = float(df.at[label, "Bruttobetrag (EUR)"])
        m_type     = str(df.at[label, "Steuerschlüssel"])

        mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, m_type)
        df.at[label, "USt/Vorsteuer 19%"] = mwst_19
        df.at[label, "Vorsteuer 7%"]  = mwst_7
        df.at[label, "Nettobetrag (Haben)"]    = netto

        df.at[label, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Rechnungsdatum"]), str(df.at[label, "Verkäufer"]), brutto_eur,
            str(df.at[label, "Zahlart (DATEV)"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "🔗 Verknüpfte Ausgangs-INV"])
        )

    st.session_state.edited_receipts = df

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText", "Is_Kreditkarte"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=True)
        
        ws = writer.sheets["DATEV_Export"]
        HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
        HEADER_FONT  = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        thin = Side(style="thin", color="D9D9D9")
        border_style = Border(left=thin, right=thin, top=thin, bottom=thin)

        for cell in ws[1]:
            cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, border_style

        for row in ws.iter_rows(min_row=2):
            for col_idx, cell in enumerate(row, start=1):
                cell.border = border_style
                if col_idx in (5, 6, 7, 8):
                    cell.number_format = '#,##0.00" €"'
                elif col_idx == 4:
                    cell.alignment = Alignment(horizontal="right")

        for col in ws.columns:
            max_len = 0
            for cell in col:
                if cell.value is not None:
                    val_str = str(cell.value)
                    str_len = sum(2 if ord(char) > 128 else 1 for char in val_str)
                    if str_len > max_len: max_len = str_len
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 5, 16)
            
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN BENUTZEROBERFLÆCHE (UI) - 하이브리드 레이아웃 탭 구조
# ══════════════════════════════════════════════════════════════════════════════

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1:
    default_zahlart: str = st.radio("⚙️ Standard-Zahlungsweg", options=ZAHLART_OPTIONS, index=0, horizontal=True)
with col_cfg2:
    selected_skr: str = st.radio("📊 Standardkontenrahmen (SKR)", options=["SKR03", "SKR04"], index=0, horizontal=True)

# 메인 화면과 설정창 화면을 기존의 다른 기능에 방해를 주지 않는 완전히 분리된 독립 탭 구조로 개편
tab_dashboard, tab_rules_setup = st.tabs(["📊 DATEV 파싱 및 파일 다운로드", f"⚙️ {selected_skr} 단골 거래처 수동 지정 설정창"])

with tab_dashboard:
    uploaded_files = st.file_uploader("📂 Belege und Rechnungen hochladen (PDF, PNG, JPG, JPEG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

    if uploaded_files:
        batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}"
        
        if st.session_state.get("last_batch_key") != batch_key:
            st.session_state.last_batch_key = batch_key
            st.session_state.edited_receipts = None

        if st.session_state.get("edited_receipts") is None:
            rows = []
            total_files = len(uploaded_files)
            progress_bar = st.progress(0)

            with st.spinner("🔮 Verarbeite Dokumente via Kognitiver AI-Engine..."):
                for idx, uploaded_file in enumerate(uploaded_files):
                    file_bytes = uploaded_file.read()
                    ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                    mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                    res = ask_gemini_vision_cached(file_bytes, mime_type, selected_skr, API_KEY)
                    beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type, raw_text = res[0], res[1], res[2], res[3], res[4], res[5], res[6], res[7]
                    was_called = res[8] if len(res) > 8 else False

                    # [신규 추가 적용] 수동 데이터베이스 규칙 및 가독성 포맷 전치 처리 적용
                    fixed_date, fixed_invoice, matched_skr = assign_readability_and_rules(vendor, date_str, beleg_nr, selected_skr)
                    if matched_skr:
                        kategorie = matched_skr
                    if fixed_date:
                        date_str = fixed_date
                    if fixed_invoice:
                        beleg_nr = fixed_invoice

                    brutto_eur = total  
                    mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, mwst_type)
                    is_cc_initial = (default_zahlart == "Kreditkarte")

                    rows.append({
                        "Rechnungsdatum":  date_str,
                        "Verkäufer":        vendor,
                        f"{selected_skr}": kategorie, 
                        "Beleg-Soll (Original)": f"{total:,.2f} $" if currency == "USD" else f"{total:,.2f} €", 
                        "Bruttobetrag (EUR)": brutto_eur,      
                        "USt/Vorsteuer 19%":  mwst_19,
                        "Vorsteuer 7%":   mwst_7,
                        "Nettobetrag (Haben)":      netto,
                        "Is_Kreditkarte":   is_cc_initial, 
                        "Zahlart (DATEV)":          default_zahlart,
                        "Steuerschlüssel":        mwst_type,
                        "Beleg_Nr":        beleg_nr,
                        "🔗 Verknüpfte Ausgangs-INV":  "",
                        "Zukünftiger DATEV-Dateiname": build_datev_filename(date_str, vendor, brutto_eur, default_zahlart, beleg_nr, ""),
                        "_FileExt": ext,
                        "_RawBytes": file_bytes,
                        "_OcrText": raw_text
                    })
                    progress_bar.progress(int((idx + 1) / total_files * 100))
                    
                    if was_called and total_files > 1 and idx < total_files - 1: 
                        time.sleep(FREE_TIER_DELAY)

            st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
            st.session_state.edited_receipts.index.name = "Nr."

        # INTERAKTIVE DATEV-ERFASSUNGSMASKE (DATA EDITOR)
        # 기능 5: 사용자 편의를 보장하기 위해 명확한 흐름으로 순차 구성된 컬럼 세팅 출력
        st.data_editor(
            st.session_state.edited_receipts,
            use_container_width=True,
            num_rows="fixed",
            height=400,
            key="beleg_editor_key",
            on_change=on_table_edited,
            column_config={
                "Rechnungsdatum": st.column_config.TextColumn("Rechnungsdatum (날짜)", width="medium"),
                "Beleg_Nr": st.column_config.TextColumn("Beleg_Nr (인보이스번호)", width="medium"),
                "Verkäufer": st.column_config.TextColumn("Verkäufer (판매처)", width="medium"),
                f"{selected_skr}": st.column_config.TextColumn(f"📊 {selected_skr} (계정과목)", width="large"),
                "Beleg-Soll (Original)":    st.column_config.TextColumn("Beleg-Soll (Original)", disabled=True), 
                "Bruttobetrag (EUR)":    st.column_config.NumberColumn("Bruttobetrag (EUR) (가격)", format="%,.2f €"),
                "USt/Vorsteuer 19%":  st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%,.2f €"),
                "Vorsteuer 7%":   st.column_config.NumberColumn("Vorsteuer 7%", format="%,.2f €"),
                "Nettobetrag (Haben)":     st.column_config.NumberColumn("Nettobetrag (Haben)", format="%,.2f €"),
                "Is_Kreditkarte":  st.column_config.CheckboxColumn("💳", help="Aktiviert = Kreditkarte, Deaktiviert = Firmenkonto"),
                "Zahlart (DATEV)":         st.column_config.TextColumn("Zahlart (DATEV) (zahlungsart)", disabled=True, width="small"),
                "Steuerschlüssel":       st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"], width="small"),
                "🔗 Verknüpfte Ausgangs-INV":  st.column_config.TextColumn("🔗 Verknüpfte Ausgangs-INV"),
                "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", width="max"),
                "_FileExt":        None, "_RawBytes": None, "_OcrText": None
            },
        )

        # EXPORT-BEREICH
        df_final = st.session_state.edited_receipts
        today = datetime.now().strftime("%Y%m%d")

        st.subheader("📥 Bereitstellung der DATEV-Exportdateien")
        col_dl1, col_dl2 = st.columns(2)
        
        with col_dl1:
            st.download_button(label=f"📊 Buchungsliste als Excel-Export herunterladen (.xlsx)", data=build_excel_bytes(df_final), file_name=f"DATEV_{selected_skr}_Buchungsliste_{today}.xlsx", use_container_width=True)
            
        with col_dl2:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for _, row in df_final.iterrows():
                    target_filename = row["Zukünftiger DATEV-Dateiname"]
                    orig_bytes = row["_RawBytes"]
                    orig_ext = row["_FileExt"]
                    ocr_log = row["_OcrText"]
                    
                    sandwich_pdf_bytes = create_sandwich_pdf(orig_bytes, orig_ext, ocr_log)
                    zip_file.writestr(target_filename, sandwich_pdf_bytes)
                    
            zip_buffer.seek(0)
            st.download_button(label="📁 PDF-Belege (Searchable Sandwich-PDFs) als ZIP-Archiv herunterladen (.zip)", data=zip_buffer.getvalue(), file_name=f"DATEV_Digitale_Belege_{today}.zip", use_container_width=True, type="primary")

# ----------------------------------------------------
# 탭 2: 단골 거래처를 수동으로 입력하고 영구 저장하는 전용 설정창 UI
# ----------------------------------------------------
with tab_rules_setup:
    st.subheader(f"⚙️ {selected_skr} 지정 거래처 전용 마스터 데이터 기입창")
    st.write("특정 단골 거래처 키워드와 우선 적용 코드를 등록하면, AI 모델 결과값 탐색보다 이 수동 데이터 규칙이 최우선으로 즉시 매칭됩니다.")
    
    current_mapping = load_mapping()
    
    with st.form("user_custom_vendor_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            v_key = st.text_input("거래처 키워드 (예: DHL, Post, Google)", placeholder="영수증 내 포함될 문자열")
        with col2:
            c_key = st.text_input(f"최우선순위 {selected_skr} 코드 번호", placeholder="예: 4730 또는 6830")
        with col3:
            n_key = st.text_input("계정과목명 레이블 명칭", placeholder="예: Portokosten")
            
        submit = st.form_submit_button("➕ 매칭 규칙 로컬 데이터에 추가")
        
        if submit:
            if v_key.strip() and c_key.strip():
                new_rule = {
                    "판매처_키워드": v_key.strip(),
                    "계정과목명": n_key.strip() if n_key.strip() else "User Defined Account",
                    "SKR04_코드": c_key.strip() if selected_skr == "SKR04" else "",
                    "SKR03_코드": c_key.strip() if selected_skr == "SKR03" else ""
                }
                current_mapping = pd.concat([current_mapping, pd.DataFrame([new_rule])], ignore_index=True)
                save_mapping(current_mapping)
                st.success(f"✔️ 규칙 저장 완료: {v_key} 키워드가 감지되면 자동으로 {c_key} 코드를 지정합니다.")
                st.rerun()
            else:
                st.error("⚠️ 거래처 키워드와 해당 계정 코드는 필수 필드입니다.")

    st.write("---")
    st.subheader("📋 현재 영구 저장되어 작동 중인 사용자 지정 마스터 룰 테이블")
    st.write("표 내부 셀을 더블클릭하여 바로 텍스트 값을 실시간 편집할 수 있으며 행을 일괄 제어할 수 있습니다.")
    
    if not current_mapping.empty:
        updated_editor_df = st.data_editor(current_mapping, num_rows="dynamic", use_container_width=True, key="hybrid_rule_editor")
        if st.button("💾 수정한 테이블 규칙 전체 적용 저장"):
            save_mapping(updated_editor_df)
            st.toast("모든 거래처 매칭 규칙이 유저 장부에 완벽하게 동기화되었습니다!")
            st.rerun()
    else:
        st.info("현재 기입된 커스텀 수동 매칭 규칙이 비어 있습니다. 필요시 단골 매칭 키워드를 작성하세요.")

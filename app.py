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

# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT PAGE SETUP
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v3.1 - DATEV-Native)")
st.caption("Automatisierte Belegerfassung mit Sandwich-PDF-Generierung und SKR-Klassifizierung für den Steuerberater.")

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
# BACKEND ENGINE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

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
        skr_guide = """
   - 3400 (Wareneinkauf)
   - 8120 (Steuerfreie Umsätze § 4 Nr. 1a UStG / Drittland-Export)
   - 4930 (Bürobedarf)
   - 4980 (Betriebsbedarf)
   - 4530 (Laufende Kfz-Betriebskosten)
   - 4660 (Reisekosten)
   - 4400 (Gebühren)"""
    else:
        skr_guide = """
   - 5400 (Wareneinkauf)
   - 4120 (Steuerfreie Umsätze § 4 Nr. 1a UStG / Drittland-Export)
   - 6815 (Bürobedarf)
   - 6300 (Sonstige betriebliche Aufwendungen)
   - 6520 (Laufende Kfz-Betriebskosten)
   - 6650 (Reisekosten)
   - 6855 (Gebühren)"""

    return f"""
Du bist ein Experte für deutsche Finanzbuchhaltung (Steuerwesen) und Belegverarbeitung nach DATEV-Standard.
Analysiere den bereitgestellten Beleg/Rechnung präzise und extrahiere die folgenden Informationen.

1. Rechnungsnummer: Rechnungs- oder Belegnummer
2. Rechnungsdatum: Ausstellungsdatum im Format YYYY-MM-DD
3. Verkäufer: Name des Kreditors/Unternehmens (max. 12 Zeichen, prägnant)
4. Bruttobetrag: Gesamtsumme (nur Zahlen, Dezimaltrenner als Punkt '.')
5. Währung: EUR oder USD
6. Kategorie_SKR: Ordne den Beleg einem der folgenden {skr_mode}-Konten zu. Gib Code und Bezeichnung an.{skr_guide}
7. MwSt_Type: Identifiziere den Steuersatz ("Split", "19_Only", "7_Only", "0_Only", "AUTO_19").

[AUSGABEFORMAT — Nur diese 7 Zeilen ohne zusätzlichen Text ausgeben, leere Werte als None]
Beleg_Nr: [Nummer]
Datum: [YYYY-MM-DD]
Vendor: [Name]
Total: [Zahl.00]
Currency: [EUR / USD]
Kategorie: [Code - Bezeichnung]
MwSt_Type: [19_Only / 7_Only / Split / AUTO_19 / 0_Only]
"""

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

def ask_gemini_vision(file_bytes: bytes, mime_type: str, skr_mode: str) -> tuple:
    default_cat = "4980 - Betriebsbedarf" if skr_mode == "SKR03" else "6300 - Sonstige Aufwendungen"
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", default_cat, "AUTO_19", "No OCR text")
    if not API_KEY: return fallback
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = get_gemini_prompt(skr_mode)
        response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, prompt_text])
        
        beleg_nr, d_str, ven, tot, cur, kat, m_type = _parse_gemini_response(response.text, default_cat)
        return beleg_nr, d_str, ven, tot, cur, kat, m_type, response.text
    except Exception:
        return fallback

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
        # 내보내기 시 메타데이터 및 토글 체크박스 열 제거
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText", "Is_Kreditkarte"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=True)
        
        ws = writer.sheets["DATEV_Export"]
        HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
        HEADER_FONT  = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        thin = Side(style="thin", color="D9D9D9")
        border_style = Border(left=thin, right=thin, top=thin, bottom=thin)

        for cell in ws[1]:
            cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, border_style

        # 📊 엑셀 열 번호 매ping 보완 (index=True이므로 1번째 열은 'Nr.'임)
        # 4: Beleg-Soll (Original) -> 문자열 데이터일 수 있으므로 우측 정렬 및 처리
        # 5: Bruttobetrag (EUR), 6: USt/Vorsteuer 19%, 7: Vorsteuer 7%, 8: Nettobetrag (Haben)
        for row in ws.iter_rows(min_row=2):
            for col_idx, cell in enumerate(row, start=1):
                cell.border = border_style
                
                # 금액 관련 컬럼 (5번째 열부터 8번째 열까지) 회계/통화 서식 강제 적용
                if col_idx in (5, 6, 7, 8):
                    # 소수점 뒤에 0이 와도 강제로 두 자리(00)를 채우고 뒤에 유로 기호 부착
                    cell.number_format = '#,##0.00_ ;[Rot]-#,##0.00_ ;"-"??_ ;@_'
                    # 또는 단순한 유로 포맷을 원하시면 아래 주석을 해제하고 사용하셔도 됩니다:
                    cell.number_format = '#,##0.00" €"'
                    
                elif col_idx == 4: # Beleg-Soll (Original)
                    cell.alignment = Alignment(horizontal="right")

        # 🗂️ 엑셀 셀 너비 자동 맞춤 기능 개선 (글자 길이에 따라 여유있게 자동 조절)
        for col in ws.columns:
            max_len = 0
            for cell in col:
                if cell.value is not None:
                    val_str = str(cell.value)
                    str_len = sum(2 if ord(char) > 128 else 1 for char in val_str)
                    if str_len > max_len:
                        max_len = str_len
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = max(max_len + 5, 16)
            
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN BENUTZEROBERFLÄCHE (UI)
# ══════════════════════════════════════════════════════════════════════════════

uploaded_files = st.file_uploader("📂 Belege und Rechnungen hochladen (PDF, PNG, JPG, JPEG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1:
    default_zahlart: str = st.radio("⚙️ Standard-Zahlungsweg", options=ZAHLART_OPTIONS, index=0, horizontal=True)
with col_cfg2:
    selected_skr: str = st.radio("📊 Standardkontenrahmen (SKR)", options=["SKR03", "SKR04"], index=0, horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}"
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

                beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type, raw_text = ask_gemini_vision(file_bytes, mime_type, selected_skr)

                brutto_eur = total  
                mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, mwst_type)

                is_cc_initial = (default_zahlart == "Kreditkarte")

                # 📌 [수정] 결과 창 테이블 헤더 명칭과 엑셀 구조를 완전히 일치화 (image_88121a.png 기준 매핑)
                rows.append({
                    "Rechnungsdatum":  date_str,
                    "Verkäufer":        vendor,
                    f"{selected_skr}": kategorie, # 💡 'Gegenkonto (SKR04)' 대신 단순히 'SKR03' 또는 'SKR04'로만 표시
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
                if total_files > 1 and idx < total_files - 1: time.sleep(FREE_TIER_DELAY)

        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # ══════════════════════════════════════════════════════════════════════════════
    # INTERAKTIVE DATEV-ERFASSUNGSMASKE (DATA EDITOR)
    # ══════════════════════════════════════════════════════════════════════════════
    # 💡 데이터프레임 원본 열 이름을 캡처 화면 명칭과 동일하게 선언했으므로 column_config 맵핑도 자동 처리됩니다.
    st.data_editor(
        st.session_state.edited_receipts,
        use_container_width=True,
        num_rows="fixed",
        height=400,
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            f"{selected_skr}": st.column_config.TextColumn(f"📊 {selected_skr}", width="medium"),
            "Beleg-Soll (Original)":    st.column_config.TextColumn("Beleg-Soll (Original)", disabled=True), 
            "Bruttobetrag (EUR)":    st.column_config.NumberColumn("Bruttobetrag (EUR)", format="%,.2f €"),
            "USt/Vorsteuer 19%":  st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%,.2f €"),
            "Vorsteuer 7%":   st.column_config.NumberColumn("Vorsteuer 7%", format="%,.2f €"),
            "Nettobetrag (Haben)":     st.column_config.NumberColumn("Nettobetrag (Haben)", format="%,.2f €"),
            "Is_Kreditkarte":  st.column_config.CheckboxColumn("💳", help="Aktiviert = Kreditkarte, Deaktiviert = Firmenkonto"),
            "Zahlart (DATEV)":         st.column_config.TextColumn("Zahlart (DATEV)", disabled=True, width="small"),
            "Steuerschlüssel":       st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"], width="small"),
            "🔗 Verknüpfte Ausgangs-INV":  st.column_config.TextColumn("🔗 Verknüpfte Ausgangs-INV"),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", width="max"),
            "_FileExt":        None, "_RawBytes": None, "_OcrText": None
        },
    )

    # EXPORT-BEREICH
    df_final = st.session_state.edited_receipts
    today = datetime.now().strftime("%Y%m%d")

    st.markdown("### 📥 Bereitstellung der DATEV-Exportdateien")
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

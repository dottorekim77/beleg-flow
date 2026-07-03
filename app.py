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
# CONFIG & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="DATEV Beleg-Parser Pro AI", page_icon="🧾", layout="wide")
st.title("🧾 Kognitiver Beleg-Parser (v4.7 - UI Formatting Fixed)")
st.caption("Automatisierte Belegerfassung mit SKR-Klassifizierung. 데이터 에디터의 화폐 단위 및 천 단위 쉼표 출력을 정상 복구했습니다.")

GEMINI_MODEL    = "gemini-3.1-flash-lite"   
FREE_TIER_DELAY = 4.2                        
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107
MAPPING_FILE    = "user_mapping.csv"

MIME_MAP = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
ZAHLART_OPTIONS = ["Firmenkonto", "Kreditkarte"]
Z_CODE_MAP      = {"Firmenkonto": "BANK", "Kreditkarte": "CC"}
_ILLEGAL_CHARS  = re.compile(r'[\\/*?:"<>|]')

# ══════════════════════════════════════════════════════════════════════════════
# CORE ENGINE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════
def load_mapping():
    if os.path.exists(MAPPING_FILE):
        try: return pd.read_csv(MAPPING_FILE)
        except: pass
    return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "SKR03_코드", "계정과목명"])

@st.cache_data(show_spinner=False)
def ask_gemini_vision_cached(file_bytes: bytes, mime_type: str, skr_mode: str, api_key_trigger: str) -> tuple:
    default_cat = "4980 - Betriebsbedarf" if skr_mode == "SKR03" else "6300 - Sonstige Aufwendungen"
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", default_cat, "AUTO_19", "No OCR text")
    if not api_key_trigger: return fallback
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt_text = f"""Du bist ein Experte für DATEV-Standard. Extrahiere die Belegdaten präzise aus dem Dokument.
Ausgabe MUSS exakt folgendes Format mit 7 Zeilen haben (keine Formatierung, kein Markdown):
Beleg_Nr: [Nummer]
Datum: [YYYY-MM-DD]
Vendor: [Name]
Total: [Zahl]
Currency: [EUR/USD]
Kategorie: [Code - Bezeichnung für {skr_mode}]
MwSt_Type: [19_Only / 7_Only / Split / AUTO_19 / 0_Only]"""

        response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, prompt_text])
        return _parse_gemini_response(response.text, default_cat) + (response.text, True)
    except:
        return fallback + (False,)

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
    except: return 0.0

def _parse_gemini_response(text: str, default_cat: str) -> tuple:
    beleg_nr = re.search(r"(?i)Beleg_Nr\s*:\s*(.*)", text)
    date_str = re.search(r"(?i)Datum\s*:\s*([\d-]+)", text)
    vendor   = re.search(r"(?i)Vendor\s*:\s*(.*)", text)
    total    = re.search(r"(?i)Total\s*:\s*([\d.,\s]+)", text)
    currency = re.search(r"(?i)Currency\s*:\s*(\w+)", text)
    kategorie= re.search(r"(?i)Kategorie\s*:\s*(.*)", text)
    mwst_type= re.search(r"(?i)MwSt_Type\s*:\s*(\w+)", text)

    res_beleg_nr = beleg_nr.group(1).strip() if beleg_nr else ""
    res_date_str = date_str.group(1).strip() if date_str else datetime.now().strftime("%Y-%m-%d")
    res_vendor   = vendor.group(1).strip() if vendor else "Unbekannt"
    res_total    = _parse_german_amount(total.group(1).strip()) if total else 0.0
    res_currency = currency.group(1).strip().upper() if currency else "EUR"
    res_kategorie= kategorie.group(1).strip() if kategorie else default_cat
    res_mwst_type= mwst_type.group(1).strip() if mwst_type else "AUTO_19"

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", res_date_str):
        res_date_str = datetime.now().strftime("%Y-%m-%d")

    return res_beleg_nr, res_date_str, res_vendor, res_total, res_currency, res_kategorie, res_mwst_type

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple:
    mwst_19, mwst_7 = 0.0, 0.0
    m_type = str(mwst_type).strip()
    if m_type in ("19_Only", "AUTO_19", "19_only", "auto_19"): 
        mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif m_type in ("7_Only", "7_only"): 
        mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif m_type in ("Split", "split"):
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
    return mwst_19, mwst_7, round(brutto_eur - (mwst_19 + mwst_7), 2)

def build_datev_filename(date_str: str, vendor: str, brutto_eur: float, zahlart: str, beleg_nr: str, inv_nr: str) -> str:
    z_code = "B" if Z_CODE_MAP.get(zahlart, "BANK") == "BANK" else "C"
    v_clean = _ILLEGAL_CHARS.sub("", vendor).replace(" ", "")[:10]
    b_suffix = f"_{_ILLEGAL_CHARS.sub('', beleg_nr)[:12]}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"-I{_ILLEGAL_CHARS.sub('', inv_nr)[:8]}" if inv_nr and inv_nr.lower() not in ("", "none") else ""
    return f"{date_str.replace('-', '')}_{v_clean}_{brutto_eur:.2f}EUR_{z_code}{b_suffix}{inv_suffix}.pdf"

def assign_readability_and_rules(vendor, date_val, inv_val, skr_mode):
    mapping_df = load_mapping()
    v_lower = str(vendor).lower() if not pd.isna(vendor) else "unbekannt"
    d_str = str(date_val).strip() if not pd.isna(date_val) else ""
    if len(d_str) == 8 and d_str.isdigit(): d_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
    i_str = str(inv_val).strip() if not pd.isna(inv_val) else ""
    if i_str.startswith('I') and not i_str.startswith('INV-'): i_str = f"INV-{i_str[1:]}"
    
    target_col = "SKR04_코드" if skr_mode == "SKR04" else "SKR03_코드"
    for _, row in mapping_df.iterrows():
        raw_keyword = str(row['판매처_키워드']) if not pd.isna(row['판매처_키워드']) else ""
        keywords = [k.strip().lower() for k in raw_keyword.split(',') if k.strip()]
        if any(k in v_lower for k in keywords):
            code = str(row[target_col]) if not pd.isna(row[target_col]) and str(row[target_col]).strip() else "9999"
            return d_str, i_str, f"{code} - {str(row['계정과목명'])}"
    return d_str, i_str, None

# ══════════════════════════════════════════════════════════════════════════════
# REKALKULATION & UI
# ══════════════════════════════════════════════════════════════════════════════
def on_table_edited():
    edit_state = st.session_state.get("beleg_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})
    if not edited_rows: return
    df = st.session_state.edited_receipts.copy()
    for idx_str, changes in edited_rows.items():
        label = df.index[int(idx_str)]
        for col, val in changes.items(): df.at[label, col] = val
        if "Is_Kreditkarte" in changes:
            df.at[label, "Zahlart (DATEV)"] = "Kreditkarte" if changes["Is_Kreditkarte"] else "Firmenkonto"
        brutto = float(df.at[label, "Bruttobetrag (EUR)"])
        m_type = str(df.at[label, "Steuerschlüssel"])
        w19, w7, net = calculate_tax_details(brutto, m_type)
        df.at[label, "USt/Vorsteuer 19%"], df.at[label, "Vorsteuer 7%"], df.at[label, "Nettobetrag (Haben)"] = w19, w7, net
        df.at[label, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Rechnungsdatum"]), str(df.at[label, "Verkäufer"]), brutto,
            str(df.at[label, "Zahlart (DATEV)"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "🔗 Verknüpfte Ausgangs-INV"])
        )
    st.session_state.edited_receipts = df

API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY: API_KEY = st.text_input("🔑 Gemini API-Key", type="password")
if API_KEY: genai.configure(api_key=API_KEY)

col1, col2 = st.columns(2)
with col1: default_zahlart = st.radio("⚙️ Standard-Zahlungsweg", options=ZAHLART_OPTIONS, index=0, horizontal=True)
with col2: selected_skr = st.radio("📊 Standardkontenrahmen (SKR)", options=["SKR03", "SKR04"], index=0, horizontal=True)

uploaded_files = st.file_uploader("📂 Belege hochladen (PDF, PNG, JPG, JPEG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key = batch_key
        st.session_state.edited_receipts = None

    if st.session_state.get("edited_receipts") is None:
        rows = []
        total_files = len(uploaded_files)
        
        progress_bar = st.progress(0)
        status_text = st.empty()

        with st.spinner("🔮 Verarbeite Dokumente via Kognitiver AI-Engine..."):
            for idx, f in enumerate(uploaded_files):
                status_text.text(f"파일 분석 중 ({idx + 1}/{total_files}): {f.name}")
                
                fb = f.read()
                ext = f.name.rsplit(".", 1)[-1].lower()
                res = ask_gemini_vision_cached(fb, MIME_MAP.get(ext, "application/octet-stream"), selected_skr, API_KEY)
                b_nr, d_str, ven, tot, cur, kat, m_type, r_txt = res[0], res[1], res[2], res[3], res[4], res[5], res[6], res[7]
                was_called = res[8] if len(res) > 8 else False

                fd, fi, m_skr = assign_readability_and_rules(ven, d_str, b_nr, selected_skr)
                if m_skr: kat = m_skr
                if fd: d_str = fd
                if fi: b_nr = fi

                w19, w7, net = calculate_tax_details(tot, m_type)
                rows.append({
                    "Rechnungsdatum": d_str, "Verkäufer": ven, f"{selected_skr}": kat,
                    "Bruttobetrag (EUR)": tot, "USt/Vorsteuer 19%": w19, "Vorsteuer 7%": w7, "Nettobetrag (Haben)": net,
                    "Is_Kreditkarte": (default_zahlart == "Kreditkarte"), "Zahlart (DATEV)": default_zahlart,
                    "Steuerschlüssel": m_type, "Beleg_Nr": b_nr, "🔗 Verknüpfte Ausgangs-INV": "",
                    "Zukünftiger DATEV-Dateiname": build_datev_filename(d_str, ven, tot, default_zahlart, b_nr, ""),
                    "_FileExt": ext, "_RawBytes": fb, "_OcrText": r_txt
                })
                
                progress_bar.progress(int((idx + 1) / total_files * 100))
                if was_called and len(uploaded_files) > 1 and idx < len(uploaded_files) - 1: 
                    time.sleep(FREE_TIER_DELAY)
                    
        status_text.empty()
        progress_bar.empty()
        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows)+1))

    # 🔍 [화폐 기호 & 천 단위 쉼표 완벽 바인딩 복구 완료]
    st.data_editor(
        st.session_state.edited_receipts, 
        use_container_width=True, 
        height=400, 
        key="beleg_editor_key", 
        on_change=on_table_edited,
        column_config={
            "Rechnungsdatum": st.column_config.TextColumn("Rechnungsdatum", width="medium"),
            "Beleg_Nr": st.column_config.TextColumn("Beleg_Nr", width="medium"),
            "Verkäufer": st.column_config.TextColumn("Verkäufer", width="medium"),
            f"{selected_skr}": st.column_config.TextColumn(f"📊 {selected_skr}", width="large"),
            "Bruttobetrag (EUR)": st.column_config.NumberColumn("Bruttobetrag (EUR)", format="%,.2f €"),
            "USt/Vorsteuer 19%": st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%,.2f €"),
            "Vorsteuer 7%": st.column_config.NumberColumn("Vorsteuer 7%", format="%,.2f €"),
            "Nettobetrag (Haben)": st.column_config.NumberColumn("Nettobetrag (Haben)", format="%,.2f €"),
            "Is_Kreditkarte": st.column_config.CheckboxColumn("💳"),
            "Zahlart (DATEV)": st.column_config.TextColumn("Zahlart (DATEV)", disabled=True, width="small"),
            "Steuerschlüssel": st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"], width="small"),
            "🔗 Verknüpfte Ausgangs-INV": st.column_config.TextColumn("🔗 Verknüpfte Ausgangs-INV"),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", width="max"),
            "_FileExt": None, "_RawBytes": None, "_OcrText": None
        }
    )

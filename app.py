import io
import time
import zipfile
from datetime import datetime
import pandas as pd
import streamlit as st
from pypdf import PdfReader, PdfWriter
from PIL import Image

# 💡 백엔드 분리 코어 모듈 임포트
from backend.ocr import configure_gemini, get_gemini_prompt, parse_gemini_response, GEMINI_MODEL
from backend.tax import calculate_tax_details
from backend.datev import build_datev_filename, build_excel_bytes

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG & INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE, PAGE_ICON = "DATEV Beleg-Parser Pro AI", "🧾"
FREE_TIER_DELAY = 4.2
MIME_MAP = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}

INITIAL_VENDORS = {
    "Shell":  {"SKR03": "4530 - Kfz-Betriebskosten", "SKR04": "6520 - Kfz-Betriebskosten"},
    "Google": {"SKR03": "4920 - Telefon", "SKR04": "6815 - Bürobedarf"},
}

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.markdown("<style>[data-testid='stSidebarNav'], section[data-testid='stSidebar'] {display: none !important;}</style>", unsafe_allow_html=True)
st.title(f"{PAGE_ICON} Modularer Beleg-Parser Engine")

# 세션 상태 보관소 관리
if "custom_rules" not in st.session_state: st.session_state.custom_rules = INITIAL_VENDORS.copy()
if "custom_zahlungswege" not in st.session_state: st.session_state.custom_zahlungswege = ["Firmenkonto", "Kreditkarte"]
if "edited_receipts" not in st.session_state: st.session_state.edited_receipts = None

API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    API_KEY = st.text_input("🔑 Gemini API-Key eingeben", type="password")
configure_gemini(API_KEY)

# ══════════════════════════════════════════════════════════════════════════════
# UI EVENT HANDLERS (CONTROLLER)
# ══════════════════════════════════════════════════════════════════════════════

def get_assigned_account(vendor_name: str, skr_mode: str) -> str:
    v_upper = vendor_name.upper()
    for keyword, accounts in st.session_state.custom_rules.items():
        if keyword.upper() in v_upper: return accounts[skr_mode]
    return ""

def on_table_edited() -> None:
    edit_state = st.session_state.get("beleg_editor_key", {})
    edited_rows, deleted_rows = edit_state.get("edited_rows", {}), edit_state.get("deleted_rows", [])
    if not edited_rows and not deleted_rows: return

    df = st.session_state.edited_receipts.copy()
    if deleted_rows:
        df = df.drop(index=[df.index[int(idx)] for idx in deleted_rows]).reset_index(drop=True)
        df.index = range(1, len(df) + 1)
        df.index.name = "Nr."
        st.session_state.edited_receipts = df
        return

    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]
        for col, new_val in changes.items(): df.at[label, col] = new_val

        brutto_eur = float(df.at[label, "Bruttobetrag (EUR)"])
        mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, str(df.at[label, "Steuerschlüssel"]))
        df.at[label, "USt/Vorsteuer 19%"], df.at[label, "Vorsteuer 7%"], df.at[label, "Nettobetrag (Haben)"] = mwst_19, mwst_7, netto
        df.at[label, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Rechnungsdatum"]), str(df.at[label, "Verkäufer"]), brutto_eur,
            str(df.at[label, "Zahlweg (DATEV)"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "🔗 Ausgangs-INV"])
        )
    st.session_state.edited_receipts = df

@st.cache_data(show_spinner=False)
def ask_gemini_vision_cached(file_bytes: bytes, mime_type: str, skr_mode: str, api_key_trigger: str) -> tuple:
    if not api_key_trigger: return ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", "", "AUTO_19", "No OCR text", False)
    import google.generativeai as genai
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, get_gemini_prompt()])
    return parse_gemini_response(response.text) + (response.text, True)

def create_sandwich_pdf(file_bytes: bytes, ext: str, raw_ai_text: str) -> bytes:
    try:
        writer = PdfWriter()
        if ext in ["jpg", "jpeg", "png"]:
            img = Image.open(io.BytesIO(file_bytes))
            img_buf = io.BytesIO()
            img.convert("RGB").save(img_buf, format="PDF")
            img_buf.seek(0)
            page = PdfReader(img_buf).pages[0]
        else: page = PdfReader(io.BytesIO(file_bytes)).pages[0]
        writer.add_page(page)
        writer.add_metadata({"/Title": "DATEV Searchable Beleg", "/Subject": raw_ai_text.replace("\n", " ")})
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception: return file_bytes

# ══════════════════════════════════════════════════════════════════════════════
# VIEW ELEMENTS
# ══════════════════════════════════════════════════════════════════════════════
col_m1, col_m2 = st.columns(2)
with col_m1:
    with st.expander("💼 Buchungsregeln", expanded=False):
        with st.form("rule_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            v_in = c1.text_input("Vendor")
            s3_in = c2.text_input("SKR03")
            s4_in = c3.text_input("SKR04")
            if st.form_submit_button("Speichern") and v_in:
                st.session_state.custom_rules[v_in] = {"SKR03": s3_in, "SKR04": s4_in}
                st.rerun()
with col_m2:
    with st.expander("💳 Zahlungswege", expanded=False):
        with st.form("zw_form", clear_on_submit=True):
            zw_in = st.text_input("Neuer Zahlungsweg")
            if st.form_submit_button("Hinzufügen") and zw_in:
                if zw_in not in st.session_state.custom_zahlungswege:
                    st.session_state.custom_zahlungswege.append(zw_in)
                    st.rerun()

st.markdown("---")
uploaded_files = st.file_uploader("📂 Belege hochladen", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
cc1, cc2 = st.columns(2)
default_zahlart = cc1.radio("Zahlungsweg Standard", options=st.session_state.custom_zahlungswege, index=0, horizontal=True)
selected_skr = cc2.radio("SKR Standard", options=["SKR03", "SKR04"], index=1, horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key, st.session_state.edited_receipts = batch_key, None

    if st.session_state.get("edited_receipts") is None:
        rows = []
        tot = len(uploaded_files)
        p_bar = st.progress(0)
        
        for idx, f in enumerate(uploaded_files):
            f_bytes = f.read()
            ext = f.name.rsplit(".", 1)[-1].lower()
            res = ask_gemini_vision_cached(f_bytes, MIME_MAP.get(ext, "application/octet-stream"), selected_skr, API_KEY)
            beleg_nr, d_str, vendor, total, currency, _, mwst_type, raw_text, was_called = res
            
            assigned_kat = get_assigned_account(vendor, selected_skr)
            mwst_19, mwst_7, netto = calculate_tax_details(total, mwst_type)
            
            rows.append({
                "Rechnungsdatum": date_str if (date_str := d_str) else datetime.now().strftime("%Y-%m-%d"),
                "Verkäufer": vendor, f"{selected_skr}": assigned_kat,
                "Beleg-Soll (Orig.)": f"{total:,.2f} $" if currency == "USD" else f"{total:,.2f} €",
                "Bruttobetrag (EUR)": total, "USt/Vorsteuer 19%": mwst_19, "Vorsteuer 7%": mwst_7, "Nettobetrag (Haben)": netto,
                "Zahlweg (DATEV)": default_zahlart, "Steuerschlüssel": mwst_type, "Beleg_Nr": beleg_nr, "🔗 Ausgangs-INV": "",
                "Zukünftiger DATEV-Dateiname": build_datev_filename(d_str, vendor, total, default_zahlart, beleg_nr, ""),
                "_FileExt": ext, "_RawBytes": f_bytes, "_OcrText": raw_text
            })
            p_bar.progress(int((idx + 1) / tot * 100))
            if was_called and tot > 1 and idx < tot - 1: time.sleep(FREE_TIER_DELAY)
            
        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    st.data_editor(
        st.session_state.edited_receipts, use_container_width=True, num_rows="dynamic", height=400, key="beleg_editor_key", on_change=on_table_edited,
        column_config={
            f"{selected_skr}": st.column_config.TextColumn(f"📊 {selected_skr}", width="medium", help="👉 Steuerberater-Konto"),
            "Beleg-Soll (Orig.)": st.column_config.TextColumn("Beleg-Soll (Orig.)", disabled=True),
            "Bruttobetrag (EUR)": st.column_config.NumberColumn("Bruttobetrag (EUR)", format="%,.2f €"),
            "USt/Vorsteuer 19%": st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%,.2f €"),
            "Vorsteuer 7%": st.column_config.NumberColumn("Vorsteuer 7%", format="%,.2f €"),
            "Nettobetrag (Haben)": st.column_config.NumberColumn("Nettobetrag (Haben)", format="%,.2f €"),
            "Zahlweg (DATEV)": st.column_config.SelectboxColumn("Zahlweg (DATEV)", options=st.session_state.custom_zahlungswege, width="medium"),
            "Steuerschlüssel": st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"]),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("Zukünftiger DATEV-Dateiname", width="max"),
            "_FileExt": None, "_RawBytes": None, "_OcrText": None
        }
    )

    df_final = st.session_state.edited_receipts
    st.markdown("### 📥 Export")
    dl1, dl2 = st.columns(2)
    dl1.download_button("📊 Excel-Export", data=build_excel_bytes(df_final), file_name=f"DATEV_{selected_skr}_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)
    
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        for _, r in df_final.iterrows():
            z.writestr(r["Zukünftiger DATEV-Dateiname"], create_sandwich_pdf(r["_RawBytes"], r["_FileExt"], r["_OcrText"]))
    dl2.download_button("📁 PDF-ZIP Export", data=zip_buf.getvalue(), file_name=f"DATEV_Belege_{datetime.now().strftime('%Y%m%d')}.zip", use_container_width=True, type="primary")

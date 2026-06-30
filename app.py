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
# 상수 / 설정
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DE Beleg-Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"   
FREE_TIER_DELAY = 4.2                        
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107

# 💡 통화 심볼 매핑 딕셔너리
CURRENCY_META = {
    "EUR": "€",
    "USD": "$",
}

MIME_MAP = {
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
}

ZAHLART_OPTIONS = ["Firmenkonto", "Mastercard"]
Z_CODE_MAP      = {"Firmenkonto": "BANK", "Mastercard": "CC"}

_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

# ══════════════════════════════════════════════════════════════════════════════
# Streamlit 페이지 설정
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v2.9-Currency Unified)")
st.caption("Brutto 원본 금액에 통화 심볼을 즉각 병합하여 직관성을 극대화한 DATEV 마스터 에디션.")

# ══════════════════════════════════════════════════════════════════════════════
# API 키 및 기본 세팅
# ══════════════════════════════════════════════════════════════════════════════
API_KEY: str = st.secrets.get("GEMINI_API_KEY", "") or st.sidebar.text_input(
    "Gemini API Key", type="password"
)

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.sidebar.warning("⚠️ Streamlit Secrets에 GEMINI_API_KEY를 설정하거나 왼쪽에 직접 입력해 주세요.")

st.sidebar.markdown("---")
st.sidebar.subheader("💱 환율 설정 (USD → EUR)")
usd_to_eur_rate: float = st.sidebar.number_input(
    "1 USD = ? EUR", min_value=0.01, max_value=10.0, value=0.92, step=0.001, format="%.4f"
)

# ══════════════════════════════════════════════════════════════════════════════
# 샌드위치 PDF 생성 엔진 및 헬퍼 함수
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
            "/Keywords": "DATEV, OCR, SandwichPDF"
        })
        output_buf = io.BytesIO()
        writer.write(output_buf)
        return output_buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Sandwich PDF 생성 실패: {e}")
        return file_bytes

def sanitize_filename(text: str) -> str:
    return _ILLEGAL_CHARS.sub("", text).strip()

def build_datev_filename(
    date_str: str, vendor: str, brutto: float, currency: str, zahlart: str, beleg_nr: str, inv_nr: str
) -> str:
    z_code = "B" if Z_CODE_MAP.get(zahlart, "BANK") == "BANK" else "C"
    c_symbol = "$" if currency == "USD" else "€"
    v_clean = sanitize_filename(vendor).replace(" ", "")[:10]
    
    b_suffix = f"_{sanitize_filename(beleg_nr)[:12]}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"-I{sanitize_filename(inv_nr)[:8]}" if inv_nr and inv_nr.lower() not in ("", "none") else ""
    
    date_compact = date_str.replace("-", "")
    return f"{date_compact}_{v_clean}_{brutto:.2f}{c_symbol}_{z_code}{b_suffix}{inv_suffix}.pdf"

def get_gemini_prompt(skr_mode: str) -> str:
    if skr_mode == "SKR03":
        skr_guide = """
   - 3400 (Wareneinkauf / 한국 수출용 또는 재판매용 상품 매입)
   - 8120 (Steuerfreie Umsätze / 한국 고객 대상 부가세 0% 면세 수출 매출 전표)
   - 4930 (Bürobedarf / 내부 사무실 소비용 필기구, 일회성 전자기기 등)
   - 4980 (Betriebsbedarf / 기타 소모품)
   - 4530 (Laufende Kfz-Betriebskosten / 차량유지비-주유 등)
   - 4660 (Reisekosten / 여비교통비)
   - 4400 (Gebühren / 서비스 이용료/수수료)"""
    else:
        skr_guide = """
   - 5400 (Wareneinkauf / 한국 수출용 또는 재판매용 상품 매입)
   - 4120 (Steuerfreie Umsätze / 한국 고객 대상 부가세 0% 면세 수출 매출 전표)
   - 6815 (Bürobedarf / 내부 사무실 소비용 필기구, 일회성 전자기기 등)
   - 6300 (Sonstige betriebliche Aufwendungen / 기타 소모품)
   - 6520 (Laufende Kfz-Betriebskosten / 차량유지비-주유 등)
   - 6650 (Reisekosten / 여비교통비)
   - 6855 (Gebühren / 서비스 이용료/수수료)"""

    return f"""
너는 독일 세무 회계(Steuerwesen) 및 제3국 수출(Drittland-Export) 무역 전표 처리 전문가야.
제공된 영수증/인보이스를 철저히 분석하여 아래 규칙에 맞게 정확한 정보를 추출해줘.

1. Rechnungsnummer: 영수증/인보이스 번호
2. Rechnungsdatum: YYYY-MM-DD 형식의 발행일
3. Verkäufer: 발행 회사명 (최대 12자 내외의 핵심 식별 단어)
4. Bruttobetrag: 총 합계 금액 (숫자만, 소수점은 반드시 마침표 '.' 사용)
5. Währung: EUR 또는 USD
6. Kategorie_SKR: 독일 표준 {skr_mode} 계정 과목 중 하나를 매핑해줘.{skr_guide}
7. MwSt_Type: 부가세 내역을 확인하여 "Split", "19_Only", "7_Only", "0_Only", "AUTO_19" 중 하나로 매핑해줘.

[출력 포맷 — 아래 7줄 외 절대 다른 텍스트 금지, 빈 값은 None 표기]
Beleg_Nr: [번호]
Datum: [YYYY-MM-DD]
Vendor: [회사명]
Total: [숫자.소수점2자리]
Currency: [EUR 또는 USD]
Kategorie: [예시 구조 코드 - 이름]
MwSt_Type: [19_Only / 7_Only / Split / AUTO_19 / 0_Only]
"""

# ══════════════════════════════════════════════════════════════════════════════
# 백엔드 엔진 코어 및 실시간 테이블 업데이트 트리거
# ══════════════════════════════════════════════════════════════════════════════

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
    except Exception as exc:
        st.sidebar.error(f"❌ Gemini API 오류: {exc}")
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
# [보완 핵심] 데이터 수정 시 통화 병합 상태 유지 로직
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

        # 데이터 수정 시 내부 원본 보관 통화(Währung_Hidden)를 참조해 계산 진행
        currency = str(df.at[label, "Währung_Hidden"])
        brutto   = float(df.at[label, "Brutto"])
        m_type   = str(df.at[label, "MwSt_Type"])

        brutto_eur = round(brutto * usd_to_eur_rate, 2) if currency == "USD" else brutto
        df.at[label, "Brutto (EUR)"] = brutto_eur

        mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, m_type)
        df.at[label, "MwSt 19% (EUR)"] = mwst_19
        df.at[label, "MwSt 7% (EUR)"]  = mwst_7
        df.at[label, "Netto (EUR)"]    = netto

        df.at[label, "DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Rechnungsdatum"]), str(df.at[label, "Verkäufer"]), brutto, currency,
            str(df.at[label, "Zahlart"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "Verknüpfte_INV"])
        )

    st.session_state.edited_receipts = df

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # 내보낼 때는 세무사용 깔끔한 데이터 피딩을 위해 백엔드 히든 컬럼 제거
        df_clean = df.drop(columns=["_FileExt", "_RawBytes", "_OcrText", "Währung_Hidden"], errors="ignore")
        df_clean.to_excel(writer, sheet_name="DATEV_Export", index=True)
        
        # Excel 서식 지정
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
                if col_idx in (5, 6, 7, 8, 9):  
                    cell.number_format = '#,##0.00'

        for col in ws.columns:
            max_len = max((len(str(c.value)) for c in col if c.value is not None), default=0)
            ws.column_dimensions[col[0].column_letter].width = max(max_len + 3, 13)
            
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# 메인 UI 대시보드
# ══════════════════════════════════════════════════════════════════════════════

uploaded_files = st.file_uploader("📂 매입 영수증 및 매출 Invoice 파일들을 함께 올려주세요", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

col_cfg1, col_cfg2 = st.columns(2)
with col_cfg1:
    default_zahlart: str = st.radio("⚙️ 기본 결제수단 지정", options=ZAHLART_OPTIONS, index=0, horizontal=True)
with col_cfg2:
    selected_skr: str = st.radio("📊 세무사 지정 계정계 (Standardkontenrahmen)", options=["SKR03", "SKR04"], index=0, horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key = batch_key
        st.session_state.edited_receipts = None

    if st.session_state.get("edited_receipts") is None:
        rows = []
        total_files = len(uploaded_files)
        progress_bar = st.progress(0)

        with st.spinner(f"🔮 통화 통합형 파싱 엔진 구동 중..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type, raw_text = ask_gemini_vision(file_bytes, mime_type, selected_skr)

                brutto_eur = round(total * usd_to_eur_rate, 2) if currency == "USD" else total
                mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, mwst_type)

                rows.append({
                    "Rechnungsdatum":  date_str,
                    "Verkäufer":        vendor,
                    f"Kategorie ({selected_skr})": kategorie,
                    "Brutto":          total,           # 🛠️ 수동 수정이 가능하도록 순수 숫자 데이터 유지
                    "Währung_Hidden":  currency,        # 🛠️ 백엔드 연산용 프라이빗 통화 키 저장고
                    "Brutto (EUR)":    brutto_eur,
                    "MwSt 19% (EUR)":  mwst_19,
                    "MwSt 7% (EUR)":   mwst_7,
                    "Netto (EUR)":      netto,
                    "Zahlart":          default_zahlart,
                    "MwSt_Type":        mwst_type,
                    "Beleg_Nr":        beleg_nr,
                    "Verknüpfte_INV":  "",
                    "DATEV-Dateiname": build_datev_filename(date_str, vendor, total, currency, default_zahlart, beleg_nr, ""),
                    "_FileExt": ext,
                    "_RawBytes": file_bytes,
                    "_OcrText": raw_text
                })
                progress_bar.progress(int((idx + 1) / total_files * 100))
                if total_files > 1 and idx < total_files - 1: time.sleep(FREE_TIER_DELAY)

        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # ══════════════════════════════════════════════════════════════════════════════
    # 컬럼 정렬 설계: Währung 컬럼을 완전히 삭제하고, 원본 금액 뒤에 동적 기호 접미사 처리
    # ══════════════════════════════════════════════════════════════════════════════
    st.data_editor(
        st.session_state.edited_receipts,
        use_container_width=True,
        num_rows="fixed",
        height=450,
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            f"Kategorie ({selected_skr})": st.column_config.TextColumn(f"🧾 {selected_skr} 과목", width="medium"),
            # 🎯 핵심 튜닝: 원본 통화(EUR/USD)종류를 감지하여 금액 에디터 우측에 접미사(Symbol) 자동 병합 표기!
            "Brutto":          st.column_config.NumberColumn("Brutto (원본)", format="%,.2f"), 
            "Brutto (EUR)":    st.column_config.NumberColumn("Brutto (EUR)", format="%,.2f €"),
            "MwSt 19% (EUR)":  st.column_config.NumberColumn("MwSt 19%", format="%,.2f €"),
            "MwSt 7% (EUR)":   st.column_config.NumberColumn("MwSt 7%", format="%,.2f €"),
            "Netto (EUR)":     st.column_config.NumberColumn("Netto (EUR)", format="%,.2f €"),
            "MwSt_Type":       st.column_config.SelectboxColumn("Tax Type", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"], width="small"),
            "Verknüpfte_INV":  st.column_config.TextColumn("🔗 연관 매출INV (수출 짝매칭 번호)"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV 확정 파일명", width="max"),
            "Währung_Hidden":  None, "_FileExt": None, "_RawBytes": None, "_OcrText": None
        },
    )

    # 아웃풋 스트림 파트
    df_final = st.session_state.edited_receipts
    today = datetime.now().strftime("%Y%m%d")

    st.markdown("### 📥 세무사 제출용 최종 아웃풋 파일 아카이브 생성")
    col_dl1, col_dl2 = st.columns(2)
    
    with col_dl1:
        st.download_button(label=f"📊 {selected_skr} 매핑형 정밀 Excel 다운로드 (.xlsx)", data=build_excel_bytes(df_final), file_name=f"DATEV_{selected_skr}_Export_{today}.xlsx", use_container_width=True)
        
    with col_dl2:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for _, row in df_final.iterrows():
                target_filename = row["DATEV-Dateiname"]
                orig_bytes = row["_RawBytes"]
                orig_ext = row["_FileExt"]
                ocr_log = row["_OcrText"]
                
                sandwich_pdf_bytes = create_sandwich_pdf(orig_bytes, orig_ext, ocr_log)
                zip_file.writestr(target_filename, sandwich_pdf_bytes)
                
        zip_buffer.seek(0)
        st.download_button(label="📁 OCR 샌드위치 PDF 일괄 변환 ZIP 파일 다운로드 (.zip)", data=zip_buffer.getvalue(), file_name=f"DATEV_Belege_Package_{today}.zip", use_container_width=True, type="primary")

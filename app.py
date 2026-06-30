import io
import re
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import google.generativeai as genai
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ══════════════════════════════════════════════════════════════════════════════
# 상수 / 설정
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DE Beleg-Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"   
FREE_TIER_DELAY = 4.2                        
# 독일 표준 부가세율
MWST_19_FACTOR  = 19 / 119
MWST_7_FACTOR   = 7 / 107

CURRENCY_META = {
    "EUR": ("€",  "EUR"),
    "USD": ("$",  "USD"),
}
CURRENCY_OPTIONS = list(CURRENCY_META.keys())

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
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v2.5-SaaS Ready)")
st.caption("독일 SKR03 세무 계정 자동 분류 및 19%/7% 다중 부가세 분리 엔진이 탑재된 고급 빌드.")

# ══════════════════════════════════════════════════════════════════════════════
# API 키 로드
# ══════════════════════════════════════════════════════════════════════════════
API_KEY: str = st.secrets.get("GEMINI_API_KEY", "") or st.sidebar.text_input(
    "Gemini API Key", type="password"
)

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.sidebar.warning("⚠️ Streamlit Secrets에 GEMINI_API_KEY를 설정하거나 왼쪽에 직접 입력해 주세요.")

# 사이드바 설정
st.sidebar.markdown("---")
st.sidebar.subheader("💱 환율 설정 (USD → EUR)")
usd_to_eur_rate: float = st.sidebar.number_input(
    "1 USD = ? EUR", min_value=0.01, max_value=10.0, value=0.92, step=0.001, format="%.4f"
)

# ══════════════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════════════

def sanitize_filename(text: str) -> str:
    return _ILLEGAL_CHARS.sub("", text).strip()

def build_datev_filename(
    date_str: str, vendor: str, brutto: float, currency: str, zahlart: str, beleg_nr: str, inv_nr: str, ext: str
) -> str:
    z_code = "B" if Z_CODE_MAP.get(zahlart, "BANK") == "BANK" else "C"
    c_symbol = "$" if currency == "USD" else "€"
    v_clean = sanitize_filename(vendor).replace(" ", "")[:10]
    
    b_suffix = f"_{sanitize_filename(beleg_nr)[:12]}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"-I{sanitize_filename(inv_nr)[:8]}" if inv_nr and inv_nr.lower() not in ("", "none") else ""
    
    date_compact = date_str.replace("-", "")
    return f"{date_compact}_{v_clean}_{brutto:.2f}{c_symbol}_{z_code}{b_suffix}{inv_suffix}.{ext}"

# ══════════════════════════════════════════════════════════════════════════════
# 고도화된 Gemini Vision 프롬프트 (SKR03 세무 범주 및 다중 부가세 추출)
# ══════════════════════════════════════════════════════════════════════════════

_GEMINI_PROMPT = """
너는 독일 세무 회계(Steuerwesen) 및 DATEV 시스템 전문가야.
제공된 문서를 분석하여 아래 규칙에 맞게 정확한 정보를 추출해줘.

1. Rechnungsnummer: 영수증/인보이스 번호
2. Rechnungsdatum: YYYY-MM-DD 형식의 발행일
3. Verkäufer: 발행 회사명 (최대 12자 내외의 핵심 식별 단어)
4. Bruttobetrag: 총 합계 금액 (숫자만, 소수점은 반드시 마침표 '.' 사용)
5. Währung: EUR 또는 USD
6. Kategorie_SKR03: 영수증의 성격을 분석하여 다음 독일 표준 SKR03 계정 과목 중 하나를 추천하고 코드와 이름을 적어줘.
   - 4930 (Bürobedarf / 사무용품)
   - 4960 (Miete/Pacht / 임차료)
   - 4650 (Bewirtungskosten / 접대비)
   - 4660 (Reisekosten / 여비교통비)
   - 4530 (Laufende Kfz-Betriebskosten / 차량유지비-주유 등)
   - 4920 (Telefon/Internet / 통신비)
   - 4400 (Gebühren / 수수료/서비스이용료)
   - 4980 (Betriebsbedarf / 기타 소모품)
7. MwSt_Split: 영수증에 기재된 부가세 내역을 확인하여 19%와 7% 금액이 각각 명시되어 있다면 그 금액을 적어줘. 만약 명시되어 있지 않고 통으로 되어 있다면 총액(Brutto)에서 역산할 수 있도록 "AUTO_19" 혹은 "AUTO_7"로 판단해줘.

[출력 포맷 — 아래 7줄 외 절대 다른 텍스트 금지, 빈 값은 None 표기]
Beleg_Nr: [번호]
Datum: [YYYY-MM-DD]
Vendor: [회사명]
Total: [숫자.소수점2자리]
Currency: [EUR 또는 USD]
Kategorie: [예: 4530 - Kfz-Kosten]
MwSt_Type: [19_Only / 7_Only / Split / AUTO_19]
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

def ask_gemini_vision(file_bytes: bytes, mime_type: str) -> tuple:
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR", "4980 - Betriebsbedarf", "AUTO_19")
    if not API_KEY: return fallback
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content([{"mime_type": mime_type, "data": file_bytes}, _GEMINI_PROMPT])
        return _parse_gemini_response(response.text)
    except Exception as exc:
        st.sidebar.error(f"❌ Gemini API 오류: {exc}")
        return fallback

def _parse_gemini_response(text: str) -> tuple:
    beleg_nr, date_str, vendor, total, currency = "", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR"
    kategorie, mwst_type = "4980 - Betriebsbedarf", "AUTO_19"
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
                if value.upper() in CURRENCY_OPTIONS: currency = value.upper()
            case "Kategorie":
                if value: kategorie = value
            case "MwSt_Type":
                if value: mwst_type = value

    return beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type

# ══════════════════════════════════════════════════════════════════════════════
# 재계산 레이어 (다중 부가세 분리 반영)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple[float, float, float]:
    """MwSt_Type에 따라 19%와 7% 부가세 및 Netto 금액을 정밀 계산합니다."""
    mwst_19, mwst_7 = 0.0, 0.0
    
    if mwst_type in ("19_Only", "AUTO_19"):
        mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only":
        mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif mwst_type == "Split":
        # 이미지에서 정확한 스플릿 비율을 못 잡은 경우 안전하게 50:50 세무 대안 분할 처리 가이드
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
        
    netto = round(brutto_eur - (mwst_19 + mwst_7), 2)
    return mwst_19, mwst_7, netto

def on_table_edited() -> None:
    edit_state  = st.session_state.get("beleg_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})
    if not edited_rows: return

    df = st.session_state.edited_receipts.copy()

    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]
        for col, new_val in changes.items():
            df.at[label, col] = new_val

        currency = str(df.at[label, "Währung"])
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
            str(df.at[label, "Zahlart"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "Verknüpfte_INV"]), str(df.at[label, "_FileExt"])
        )

    st.session_state.edited_receipts = df

# ══════════════════════════════════════════════════════════════════════════════
# Excel 포맷 엔진 (신규 컬럼 대응)
# ══════════════════════════════════════════════════════════════════════════════

def _apply_excel_styles(worksheet) -> None:
    HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
    HEADER_FONT  = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    HEADER_ALIGN = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="D9D9D9")
    border_style = Border(left=thin, right=thin, top=thin, bottom=thin)

    for cell in worksheet[1]:
        cell.fill, cell.font, cell.alignment, cell.border = HEADER_FILL, HEADER_FONT, HEADER_ALIGN, border_style

    for row in worksheet.iter_rows(min_row=2):
        for col_idx, cell in enumerate(row, start=1):
            cell.border = border_style
            if col_idx in (1, 2, 3): cell.alignment = Alignment(horizontal="center")
            elif col_idx in (5, 6, 7, 8, 9):  # 금액 컬럼 서식 지정
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = '#,##0.00'

    for col in worksheet.columns:
        max_len = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        worksheet.column_dimensions[col[0].column_letter].width = max(max_len + 3, 13)

def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="DATEV_Export", index=True)
        _apply_excel_styles(writer.sheets["DATEV_Export"])
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# UI 레이어
# ══════════════════════════════════════════════════════════════════════════════

uploaded_files = st.file_uploader("Rechnungen auswählen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
default_zahlart: str = st.radio("⚙️ 기본 결제수단", options=ZAHLART_OPTIONS, index=0, horizontal=True)

if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files)
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key = batch_key
        st.session_state.edited_receipts = None

    if st.session_state.get("edited_receipts") is None:
        rows = []
        total_files = len(uploaded_files)
        progress_bar = st.progress(0)

        with st.spinner("🔮 비전 AI가 독일 세무 범주(SKR03) 및 부가세 내역을 정밀 분석 중..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type = ask_gemini_vision(file_bytes, mime_type)

                brutto_eur = round(total * usd_to_eur_rate, 2) if currency == "USD" else total
                mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, mwst_type)

                rows.append({
                    "Rechnungsdatum":  date_str,
                    "Verkäufer":        vendor,
                    "Kategorie (SKR03)": kategorie,
                    "Währung":          currency,
                    "Brutto":          total,
                    "Brutto (EUR)":    brutto_eur,
                    "MwSt 19% (EUR)":  mwst_19,
                    "MwSt 7% (EUR)":   mwst_7,
                    "Netto (EUR)":      netto,
                    "Zahlart":          default_zahlart,
                    "MwSt_Type":        mwst_type,
                    "Beleg_Nr":        beleg_nr,
                    "Verknüpfte_INV":  "",
                    "DATEV-Dateiname": build_datev_filename(date_str, vendor, total, currency, default_zahlart, beleg_nr, "", ext),
                    "_FileExt": ext,
                })
                progress_bar.progress(int((idx + 1) / total_files * 100))
                if total_files > 1 and idx < total_files - 1: time.sleep(FREE_TIER_DELAY)

        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # 통계 배너
    df_all = st.session_state.edited_receipts
    st.markdown(f"### 📊 분석 요약: 총 {len(df_all)} 건의 전표가 정렬되었습니다.")

    # 데이터 에디터 렌더링
    st.data_editor(
        st.session_state.edited_receipts,
        use_container_width=True,
        num_rows="fixed",
        height=500,
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            "Kategorie (SKR03)": st.column_config.TextColumn("🧾 SKR03 계정과목", width="medium"),
            "Brutto":          st.column_config.NumberColumn("Brutto (원본)", format="%,.2f"),
            "Brutto (EUR)":    st.column_config.NumberColumn("Brutto (EUR)", format="%,.2f €"),
            "MwSt 19% (EUR)":  st.column_config.NumberColumn("MwSt 19%", format="%,.2f €"),
            "MwSt 7% (EUR)":   st.column_config.NumberColumn("MwSt 7%", format="%,.2f €"),
            "Netto (EUR)":     st.column_config.NumberColumn("Netto (EUR)", format="%,.2f €"),
            "MwSt_Type":       st.column_config.SelectboxColumn("Tax Type", options=["19_Only", "7_Only", "Split", "AUTO_19"], width="small"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV 파일명", width="max"),
            "_FileExt":        None,
        },
    )

    # 다운로드 레이어
    export_df = st.session_state.edited_receipts.drop(columns=["_FileExt"])
    today = datetime.now().strftime("%Y%m%d")
    
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(label="📥 DATEV 호환 Excel 다운로드 (.xlsx)", data=build_excel_bytes(export_df), file_name=f"DATEV_Export_{today}.xlsx", use_container_width=True)
    with col_dl2:
        st.download_button(label="📄 CSV 다운로드 (.csv)", data=export_df.to_csv(index=True, encoding="utf-8-sig"), file_name=f"DATEV_Export_{today}.csv", use_container_width=True)

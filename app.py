"""
DE Beleg-Parser Pro AI  (v2.2-Gemini Vision)
─────────────────────────────────────────────
독일 세무/관세 전표 AI 파싱 도구
- Gemini 3.1 Flash-Lite Vision으로 PDF·이미지 원샷 파싱
- EUR / USD 통화 자동 감지 및 USD→EUR 환율 변환
- DATEV 파일명 자동 생성 + 편집 가능 테이블
- Excel / CSV 다운로드
"""

# ── 표준 라이브러리 ─────────────────────────────────────────────────────────
import io
import re
import time
from datetime import datetime

# ── 서드파티 라이브러리 ──────────────────────────────────────────────────────
import pandas as pd
import streamlit as st
import google.generativeai as genai
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ══════════════════════════════════════════════════════════════════════════════
# 상수 / 설정
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE      = "DE Beleg-Parser Pro AI"
PAGE_ICON       = "🧾"
GEMINI_MODEL    = "gemini-3.1-flash-lite"   # 2027-05-07까지 지원되는 안정 모델
FREE_TIER_DELAY = 4.2                        # 무료 API 쓰로틀링 (초)
MWST_RATE       = 19 / 119                   # 독일 부가세 19% 역산 계수

# 지원 통화 목록  →  (표시 기호, 컬럼 레이블 접미사)
CURRENCY_META = {
    "EUR": ("€",  "EUR"),
    "USD": ("$",  "USD"),
}
CURRENCY_OPTIONS = list(CURRENCY_META.keys())   # ["EUR", "USD"]

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
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v2.2-Gemini Vision)")
st.caption("Gemini Vision 엔진으로 PDF·이미지를 직접 판독해 영수증 정보를 자동 추출합니다. EUR / USD 혼합 처리 지원.")

# ══════════════════════════════════════════════════════════════════════════════
# API 키 로드
# ══════════════════════════════════════════════════════════════════════════════
API_KEY: str = st.secrets.get("GEMINI_API_KEY", "") or st.sidebar.text_input(
    "Gemini API Key", type="password"
)

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.sidebar.warning(
        "⚠️ Google AI Studio에서 발급받은 API Key를 "
        "Streamlit Secrets에 추가하거나 왼쪽에 직접 입력해 주세요."
    )

# ══════════════════════════════════════════════════════════════════════════════
# 사이드바: USD→EUR 환율 설정
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.markdown("---")
st.sidebar.subheader("💱 환율 설정 (USD → EUR)")
usd_to_eur_rate: float = st.sidebar.number_input(
    "1 USD = ? EUR",
    min_value=0.01,
    max_value=10.0,
    value=0.92,
    step=0.001,
    format="%.4f",
    help="달러 영수증의 EUR 환산에 사용됩니다. 매일 갱신 권장.",
)
st.sidebar.caption("※ 유럽중앙은행(ECB) 기준 환율을 입력하세요.")


# ══════════════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════════════

def sanitize_filename(text: str) -> str:
    """파일명에 사용할 수 없는 특수문자를 제거합니다."""
    return _ILLEGAL_CHARS.sub("", text).strip()


def build_datev_filename(
    date_str: str,
    vendor: str,
    brutto: float,
    currency: str,
    zahlart: str,
    beleg_nr: str,
    inv_nr: str,
    ext: str,
) -> str:
    """DATEV 표준 파일명을 조합합니다. 통화 코드를 금액 뒤에 표기합니다."""
    z_code     = Z_CODE_MAP.get(zahlart, "BANK")
    b_suffix   = f"_{beleg_nr}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"_{inv_nr}"   if inv_nr  and inv_nr.lower()   not in ("", "none") else ""
    v_clean    = sanitize_filename(vendor)
    return f"{date_str}_{v_clean}_{brutto:.2f}{currency}_{z_code}{b_suffix}{inv_suffix}.{ext}"


# ══════════════════════════════════════════════════════════════════════════════
# Gemini Vision 파싱 엔진
# ══════════════════════════════════════════════════════════════════════════════

_GEMINI_PROMPT = """
너는 독일 세무 회계 및 관세 전표 전문가야.
제공된 영수증/인보이스 문서를 시각적으로 정밀하게 분석해서
아래 5가지 정보만 정확히 찾아내줘.

1. Rechnungsnummer (Belegnummer / Invoice No. — 영수증 일련번호)
2. Rechnungsdatum  (발행일, YYYY-MM-DD 형식으로 변환)
3. Verkäufer       (발행 회사명 / 판매처 이름)
4. Bruttobetrag    (총 합계 금액)
   - 반드시 숫자만, 소수점은 반드시 점(.)으로, 예: 1234.56
   - 쉼표 천단위 구분자 제거, 통화 기호(€/$) 및 EUR/USD 텍스트 제거
   - "Gesamt", "Total", "Gesamtbetrag", "Summe", "Brutto", "inkl. MwSt" 등 최종 합계 기준
   - 찾을 수 없으면 반드시 0.00 기재
5. Währung (통화 코드)
   - 문서에 € 또는 EUR 표시가 있으면 → EUR
   - 문서에 $ 또는 USD 표시가 있으면 → USD
   - 알 수 없으면 → EUR (기본값)

[출력 포맷 — 아래 5줄 외 절대 다른 텍스트 금지]
Beleg_Nr: [번호]
Datum: [YYYY-MM-DD]
Vendor: [회사명]
Total: [숫자.소수점2자리]
Currency: [EUR 또는 USD]

항목을 찾을 수 없으면 값을 비워 둘 것. Total은 반드시 0.00, Currency는 반드시 EUR 또는 USD 기재.
"""


def _parse_german_amount(raw: str) -> float:
    """
    독일/유럽식 및 영미식 금액 문자열을 float으로 변환합니다.

        "1.234,56"  → 1234.56   (독일식: 점=천단위, 쉼표=소수점)
        "1,234.56"  → 1234.56   (영미식: 쉼표=천단위, 점=소수점)
        "45,90"     → 45.90     (쉼표=소수점)
        "45.90"     → 45.90
        "€ 12.50"   → 12.50
        "$ 12.50"   → 12.50
        "12,50 EUR" → 12.50
    """
    s = re.sub(r"[€$£\s]", "", raw)
    s = re.sub(r"(?i)(eur|usd|gbp)", "", s).strip()

    if not s:
        return 0.0

    if "." in s and "," in s:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")           # 1,234.56 → 1234.56
        else:
            s = s.replace(".", "").replace(",", ".")  # 1.234,56 → 1234.56
    elif "," in s:
        after_comma = s[s.rfind(",") + 1:]
        if len(after_comma) == 2:
            s = s.replace(",", ".")          # 45,90 → 45.90
        else:
            s = s.replace(",", "")           # 1,234 → 1234

    try:
        return float(s)
    except ValueError:
        return 0.0


def ask_gemini_vision(file_bytes: bytes, mime_type: str) -> tuple[str, str, str, float, str]:
    """
    Gemini Vision API를 호출해 영수증 핵심 5개 필드를 추출합니다.

    Returns:
        (beleg_nr, date_str, vendor, total, currency)
    """
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR")

    if not API_KEY:
        return fallback

    try:
        model    = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content([
            {"mime_type": mime_type, "data": file_bytes},
            _GEMINI_PROMPT,
        ])
        result = _parse_gemini_response(response.text)

        with st.sidebar.expander(f"🔍 AI 원본 응답 ({mime_type.split('/')[-1]})", expanded=False):
            st.code(response.text, language="text")

        return result

    except Exception as exc:
        st.sidebar.error(f"❌ Gemini API 오류: {exc}")
        return fallback


def _parse_gemini_response(text: str) -> tuple[str, str, str, float, str]:
    """
    Gemini 응답 텍스트에서 5개 필드를 파싱합니다.
    마크다운 볼드·이탤릭·백틱 등 포맷 이탈에 강건하게 처리합니다.
    """
    beleg_nr = ""
    date_str = datetime.now().strftime("%Y-%m-%d")
    vendor   = "Unbekannt"
    total    = 0.0
    currency = "EUR"

    cleaned = re.sub(r"[*`]", "", text)

    for line in cleaned.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key   = key.strip()
        value = value.strip()

        match key:
            case "Beleg_Nr":
                beleg_nr = value
            case "Datum":
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    date_str = value
                else:
                    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", value)
                    if m:
                        date_str = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
            case "Vendor":
                if value:
                    vendor = value
            case "Total":
                parsed = _parse_german_amount(value)
                if parsed > 0:
                    total = parsed
            case "Currency":
                v = value.upper().strip()
                if v in CURRENCY_OPTIONS:
                    currency = v

    return beleg_nr, date_str, vendor, total, currency


# ══════════════════════════════════════════════════════════════════════════════
# 테이블 편집 콜백
# ══════════════════════════════════════════════════════════════════════════════

def on_table_edited() -> None:
    """편집된 행의 EUR 환산액·Netto 재계산 및 DATEV 파일명 갱신."""
    edit_state  = st.session_state.get("beleg_editor_key", {})
    edited_rows = edit_state.get("edited_rows", {})

    if not edited_rows:
        return

    df = st.session_state.edited_receipts.copy()

    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]

        for col, new_val in changes.items():
            df.at[label, col] = new_val

        currency = str(df.at[label, "Währung"])
        brutto   = float(df.at[label, "Brutto"])

        # EUR 환산
        if currency == "USD":
            brutto_eur = round(brutto * usd_to_eur_rate, 2)
        else:
            brutto_eur = brutto
        df.at[label, "Brutto (EUR)"] = brutto_eur

        # MwSt·Netto (EUR 기준)
        mwst = round(brutto_eur * MWST_RATE, 2)
        df.at[label, "MwSt 19% (EUR)"] = mwst
        df.at[label, "Netto (EUR)"]     = round(brutto_eur - mwst, 2)

        # DATEV 파일명 갱신
        df.at[label, "DATEV-Dateiname"] = build_datev_filename(
            date_str = str(df.at[label, "Rechnungsdatum"]),
            vendor   = str(df.at[label, "Verkäufer"]),
            brutto   = brutto,
            currency = currency,
            zahlart  = str(df.at[label, "Zahlart"]),
            beleg_nr = str(df.at[label, "Beleg_Nr"]),
            inv_nr   = str(df.at[label, "Verknüpfte_INV"]),
            ext      = str(df.at[label, "_FileExt"]),
        )

    st.session_state.edited_receipts = df


# ══════════════════════════════════════════════════════════════════════════════
# Excel 스타일링 헬퍼
# ══════════════════════════════════════════════════════════════════════════════

def _apply_excel_styles(worksheet) -> None:
    """openpyxl 워크시트에 DATEV 스타일을 적용합니다."""
    HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
    HEADER_FONT  = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    HEADER_ALIGN = Alignment(horizontal="center", vertical="center")

    def _border(bottom_style="medium", bottom_color="1F4E78"):
        thin = Side(style="thin", color="D9D9D9")
        return Border(
            left=thin, right=thin, top=thin,
            bottom=Side(style=bottom_style, color=bottom_color),
        )

    for cell in worksheet[1]:
        cell.fill      = HEADER_FILL
        cell.font      = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        cell.border    = _border("medium", "1F4E78")

    for row in worksheet.iter_rows(min_row=2):
        for col_idx, cell in enumerate(row, start=1):
            cell.border = _border("thin", "E0E0E0")
            if col_idx in (1, 2):
                cell.alignment = Alignment(horizontal="center")
            elif col_idx in (4, 5, 6, 7, 8):
                cell.alignment = Alignment(horizontal="right")

    for col in worksheet.columns:
        max_len = max(
            (len(str(c.value)) for c in col if c.value is not None), default=0
        )
        worksheet.column_dimensions[col[0].column_letter].width = max(max_len + 4, 12)


def build_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="DATEV_Export", index=True)
        _apply_excel_styles(writer.sheets["DATEV_Export"])
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# 메인 UI
# ══════════════════════════════════════════════════════════════════════════════

uploaded_files = st.file_uploader(
    "Rechnungen auswählen (PDF oder Bild)",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

default_zahlart: str = st.radio(
    "⚙️ 기본 결제수단 (Zahlart-Default)",
    options=ZAHLART_OPTIONS,
    index=0,
    horizontal=True,
    help="파일 업로드 전에 결제 방식을 선택하세요.",
)

# ── 파일 배치가 바뀌면 세션 초기화 ──────────────────────────────────────────
if uploaded_files:
    batch_key = "".join(f.name for f in uploaded_files)

    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key  = batch_key
        st.session_state.edited_receipts = None

    # ── AI 파싱 (최초 1회만 실행) ────────────────────────────────────────────
    if st.session_state.get("edited_receipts") is None:
        rows         = []
        total_files  = len(uploaded_files)
        progress_bar = st.progress(0)

        with st.spinner("🔮 Gemini Vision AI가 문서를 판독 중입니다..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                beleg_nr, date_str, vendor, total, currency = ask_gemini_vision(
                    file_bytes, mime_type
                )

                # EUR 환산 및 세금 계산
                brutto_eur = round(total * usd_to_eur_rate, 2) if currency == "USD" else total
                mwst       = round(brutto_eur * MWST_RATE, 2)

                rows.append({
                    "Rechnungsdatum":  date_str,
                    "Verkäufer":       vendor,
                    "Währung":         currency,                    # ← 통화 코드
                    "Brutto":          total,                       # ← 원본 통화 금액
                    "Brutto (EUR)":    brutto_eur,                  # ← EUR 환산액
                    "MwSt 19% (EUR)":  mwst,
                    "Netto (EUR)":     round(brutto_eur - mwst, 2),
                    "Zahlart":         default_zahlart,
                    "Beleg_Nr":        beleg_nr,
                    "Verknüpfte_INV":  "",
                    "DATEV-Dateiname": build_datev_filename(
                        date_str, vendor, total, currency,
                        default_zahlart, beleg_nr, "", ext,
                    ),
                    "_FileExt": ext,
                })

                progress_bar.progress(int((idx + 1) / total_files * 100))
                if total_files > 1 and idx < total_files - 1:
                    time.sleep(FREE_TIER_DELAY)

        df = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        df.index.name                    = "Nr."
        st.session_state.edited_receipts = df

    # ── 통계 요약 배너 ────────────────────────────────────────────────────────
    df_all = st.session_state.edited_receipts
    eur_mask = df_all["Währung"] == "EUR"
    usd_mask = df_all["Währung"] == "USD"

    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    col_s1.metric("📄 총 영수증",     f"{len(df_all)} 건")
    col_s2.metric("🇪🇺 EUR 영수증",  f"{eur_mask.sum()} 건",
                  f"합계 {df_all.loc[eur_mask, 'Brutto'].sum():,.2f} €")
    col_s3.metric("🇺🇸 USD 영수증",  f"{usd_mask.sum()} 건",
                  f"합계 {df_all.loc[usd_mask, 'Brutto'].sum():,.2f} $")
    col_s4.metric("💶 EUR 환산 합계", f"{df_all['Brutto (EUR)'].sum():,.2f} €",
                  f"환율 1 USD = {usd_to_eur_rate:.4f} EUR")

    # ── 결과 테이블 ──────────────────────────────────────────────────────────
    st.markdown("---")

    col_title, col_filter = st.columns([2, 1])
    with col_title:
        st.subheader("📊 Auswertungsübersicht")
    with col_filter:
        hide_done = st.checkbox(
            "✅ INV 연결 완료 항목 숨기기",
            value=False,
            help="Verknüpfte_INV 값이 입력된 행을 숨겨 미완료 항목만 표시합니다.",
        )

    display_df = st.session_state.edited_receipts.copy()
    if hide_done:
        mask       = display_df["Verknüpfte_INV"].isin(["", None]) | display_df["Verknüpfte_INV"].isna()
        display_df = display_df[mask]

    # USD 행은 배경색으로 시각적 구분 (Streamlit은 행별 컬러 미지원 → 통화 컬럼으로 구분)
    st.data_editor(
        display_df,
        use_container_width=True,
        num_rows="fixed",
        height=550,
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            "Rechnungsdatum":  st.column_config.TextColumn("Rechnungsdatum",            width="medium"),
            "Verkäufer":       st.column_config.TextColumn("Verkäufer",                 width="medium"),
            "Währung":         st.column_config.SelectboxColumn(
                                   "💱 Währung", options=CURRENCY_OPTIONS,
                                   width="small", required=True,
                                   help="EUR / USD — 변경 시 환산액 자동 재계산",
                               ),
            "Brutto":          st.column_config.NumberColumn("Brutto (원본)",           width="small",  format="%.2f"),
            "Brutto (EUR)":    st.column_config.NumberColumn("Brutto (EUR 환산)",       width="small",  format="%.2f €"),
            "MwSt 19% (EUR)":  st.column_config.NumberColumn("MwSt 19% (EUR)",          width="small",  format="%.2f €"),
            "Netto (EUR)":     st.column_config.NumberColumn("Netto (EUR)",              width="small",  format="%.2f €"),
            "Zahlart":         st.column_config.SelectboxColumn(
                                   "Zahlart (결제)", options=ZAHLART_OPTIONS,
                                   width="medium", required=True,
                               ),
            "Beleg_Nr":        st.column_config.TextColumn("Beleg_Nr (영수증 번호)",    width="medium"),
            "Verknüpfte_INV":  st.column_config.TextColumn("Verknüpfte_INV (매출번호)", width="medium"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV-Dateiname",           width="max"),
            "_FileExt":        None,
        },
    )

    # ── 다운로드 버튼 ────────────────────────────────────────────────────────
    export_df = st.session_state.edited_receipts.drop(columns=["_FileExt"])
    today     = datetime.now().strftime("%Y%m%d")

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            label               = "📥 Excel 다운로드 (.xlsx)",
            data                = build_excel_bytes(export_df),
            file_name           = f"DATEV_Export_{today}.xlsx",
            mime                = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width = True,
        )
    with col_dl2:
        st.download_button(
            label               = "📄 CSV 다운로드 (.csv)",
            data                = export_df.to_csv(index=True, encoding="utf-8-sig"),
            file_name           = f"DATEV_Export_{today}.csv",
            mime                = "text/csv",
            use_container_width = True,
        )

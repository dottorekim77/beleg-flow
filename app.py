"""
DE Beleg-Parser Pro AI  (v2.0-Gemini Vision)
─────────────────────────────────────────────
독일 세무/관세 전표 AI 파싱 도구
- Gemini 1.5 Flash Vision으로 PDF·이미지 원샷 파싱
- DATEV 파일명 자동 생성 + 편집 가능 테이블
- Excel / CSV 다운로드
"""

# ── 표준 라이브러리 ────────────────────────────────────────────────────────────
import io
import re
import time
from datetime import datetime

# ── 서드파티 라이브러리 ──────────────────────────────────────────────────────
import pandas as pd
import streamlit as st
import google.generativeai as genai
from openpyxl.styles import (
    Alignment, Border, Font, PatternFill, Side
)

# ══════════════════════════════════════════════════════════════════════════════
# 상수 / 설정
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE       = "DE Beleg-Parser Pro AI"
PAGE_ICON        = "🧾"
GEMINI_MODEL     = "models/gemini-1.5-flash"
FREE_TIER_DELAY  = 4.2          # 무료 API 쓰로틀링 (초)
MWST_RATE        = 19 / 119     # 독일 부가세 19% 역산 계수

MIME_MAP = {
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
}

ZAHLART_OPTIONS = ["Firmenkonto", "Mastercard"]
Z_CODE_MAP      = {"Firmenkonto": "BANK", "Mastercard": "CC"}

# ── 파일명에 사용 불가한 문자 제거용 정규식
_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')

# ══════════════════════════════════════════════════════════════════════════════
# Streamlit 페이지 설정
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(f"{PAGE_ICON} Kognitiver Beleg-Parser (v2.0-Gemini Vision)")
st.caption("Gemini Vision 엔진으로 PDF·이미지를 직접 판독해 영수증 정보를 자동 추출합니다.")

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
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════════════

def sanitize_filename(text: str) -> str:
    """파일명에 사용할 수 없는 특수문자를 제거합니다."""
    return _ILLEGAL_CHARS.sub("", text).strip()


def build_datev_filename(
    date_str: str,
    vendor: str,
    brutto: float,
    zahlart: str,
    beleg_nr: str,
    inv_nr: str,
    ext: str,
) -> str:
    """DATEV 표준 파일명을 조합합니다."""
    z_code     = Z_CODE_MAP.get(zahlart, "BANK")
    b_suffix   = f"_{beleg_nr}" if beleg_nr and beleg_nr.lower() not in ("", "none") else ""
    inv_suffix = f"_{inv_nr}"   if inv_nr  and inv_nr.lower()   not in ("", "none") else ""
    v_clean    = sanitize_filename(vendor)
    return f"{date_str}_{v_clean}_{brutto:.2f}EUR_{z_code}{b_suffix}{inv_suffix}.{ext}"


# ══════════════════════════════════════════════════════════════════════════════
# Gemini Vision 파싱 엔진
# ══════════════════════════════════════════════════════════════════════════════

_GEMINI_PROMPT = """
너는 독일 세무 회계 및 관세 전표 전문가야.
제공된 영수증/인보이스 문서를 시각적으로 정밀하게 분석해서
아래 4가지 정보만 정확히 찾아내줘.

1. Rechnungsnummer (Belegnummer / Invoice No. — 영수증 일련번호)
2. Rechnungsdatum  (발행일, YYYY-MM-DD 형식으로 변환)
3. Verkäufer       (발행 회사명 / 판매처 이름)
4. Bruttobetrag    (총 합계 금액, 유로화 기호 없이 숫자만, 예: 45.90)

[출력 포맷 — 이 형식 외 다른 텍스트 절대 금지]
Beleg_Nr: [번호]
Datum: [YYYY-MM-DD]
Vendor: [회사명]
Total: [금액]

항목을 찾을 수 없으면 값을 비워 둘 것 (예: Beleg_Nr: )
"""


def ask_gemini_vision(file_bytes: bytes, mime_type: str) -> tuple[str, str, str, float]:
    """
    Gemini Vision API를 호출해 영수증 핵심 4개 필드를 추출합니다.

    Returns:
        (beleg_nr, date_str, vendor, total)
    """
    fallback = ("", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0)

    if not API_KEY:
        return fallback

    try:
        model    = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content([
            {"mime_type": mime_type, "data": file_bytes},
            _GEMINI_PROMPT,
        ])
        return _parse_gemini_response(response.text)

    except Exception as exc:
        st.sidebar.error(f"❌ Gemini API 오류: {exc}")
        return fallback


def _parse_gemini_response(text: str) -> tuple[str, str, str, float]:
    """Gemini 응답 텍스트에서 4개 필드를 파싱합니다."""
    beleg_nr = ""
    date_str = datetime.now().strftime("%Y-%m-%d")
    vendor   = "Unbekannt"
    total    = 0.0

    for line in text.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()

        match key.strip():
            case "Beleg_Nr":
                beleg_nr = value
            case "Datum":
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    date_str = value
            case "Vendor":
                if value:
                    vendor = value
            case "Total":
                try:
                    total = float(value)
                except ValueError:
                    pass

    return beleg_nr, date_str, vendor, total


# ══════════════════════════════════════════════════════════════════════════════
# 테이블 편집 콜백
# ══════════════════════════════════════════════════════════════════════════════

def on_table_edited() -> None:
    """
    data_editor 변경 시 호출되는 콜백.
    편집된 행의 Netto 재계산 및 DATEV 파일명을 갱신합니다.
    """
    edit_state = st.session_state.get("beleg_editor_key", {})
    edited_rows: dict = edit_state.get("edited_rows", {})

    if not edited_rows:
        return

    df = st.session_state.edited_receipts.copy()

    for row_idx_str, changes in edited_rows.items():
        # data_editor는 0-based offset을 반환 → DataFrame 인덱스(1-based)로 변환
        label = df.index[int(row_idx_str)]

        for col, new_val in changes.items():
            df.at[label, col] = new_val

        # Netto 재계산
        brutto = float(df.at[label, "Brutto (€)"])
        mwst   = float(df.at[label, "MwSt 19% (€)"])
        df.at[label, "Netto (€)"] = round(brutto - mwst, 2)

        # DATEV 파일명 갱신
        df.at[label, "DATEV-Dateiname"] = build_datev_filename(
            date_str = str(df.at[label, "Rechnungsdatum"]),
            vendor   = str(df.at[label, "Verkäufer"]),
            brutto   = brutto,
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
        side_thin = Side(style="thin", color="D9D9D9")
        return Border(
            left   = side_thin,
            right  = side_thin,
            top    = side_thin,
            bottom = Side(style=bottom_style, color=bottom_color),
        )

    header_border = _border("medium", "1F4E78")
    data_border   = _border("thin",   "E0E0E0")

    # 헤더 행 스타일
    for cell in worksheet[1]:
        cell.fill      = HEADER_FILL
        cell.font      = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        cell.border    = header_border

    # 데이터 행 스타일
    for row in worksheet.iter_rows(min_row=2):
        for col_idx, cell in enumerate(row, start=1):
            cell.border = data_border
            if col_idx in (1, 2):
                cell.alignment = Alignment(horizontal="center")
            elif col_idx in (4, 5, 6):
                cell.alignment = Alignment(horizontal="right")

    # 열 너비 자동 조정
    for col in worksheet.columns:
        max_len = max(
            (len(str(cell.value)) for cell in col if cell.value is not None),
            default=0,
        )
        worksheet.column_dimensions[col[0].column_letter].width = max(max_len + 4, 12)


def build_excel_bytes(df: pd.DataFrame) -> bytes:
    """DataFrame을 스타일 적용된 .xlsx 바이트로 변환합니다."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="DATEV_Export", index=True)
        _apply_excel_styles(writer.sheets["DATEV_Export"])
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# 메인 UI
# ══════════════════════════════════════════════════════════════════════════════

# ── 파일 업로드 & 기본 결제수단 설정 ─────────────────────────────────────────
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
        st.session_state.last_batch_key    = batch_key
        st.session_state.edited_receipts   = None

    # ── AI 파싱 (최초 1회만 실행) ─────────────────────────────────────────
    if st.session_state.get("edited_receipts") is None:
        rows         = []
        total_files  = len(uploaded_files)
        progress_bar = st.progress(0)

        with st.spinner("🔮 Gemini Vision AI가 문서를 판독 중입니다..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                ext        = uploaded_file.name.rsplit(".", 1)[-1].lower()
                mime_type  = MIME_MAP.get(ext, "application/octet-stream")

                beleg_nr, date_str, vendor, total = ask_gemini_vision(file_bytes, mime_type)

                mwst = round(total * MWST_RATE, 2)

                rows.append({
                    "Rechnungsdatum":  date_str,
                    "Verkäufer":       vendor,
                    "Brutto (€)":      total,
                    "MwSt 19% (€)":    mwst,
                    "Netto (€)":       round(total - mwst, 2),
                    "Zahlart":         default_zahlart,
                    "Beleg_Nr":        beleg_nr,
                    "Verknüpfte_INV":  "",
                    "DATEV-Dateiname": build_datev_filename(
                        date_str, vendor, total,
                        default_zahlart, beleg_nr, "", ext,
                    ),
                    "_FileExt": ext,
                })

                progress_bar.progress(int((idx + 1) / total_files * 100))

                # 무료 티어 쓰로틀링 (마지막 파일 제외)
                if total_files > 1 and idx < total_files - 1:
                    time.sleep(FREE_TIER_DELAY)

        df = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        df.index.name                    = "Nr."
        st.session_state.edited_receipts = df

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

    st.data_editor(
        display_df,
        use_container_width=True,
        num_rows="fixed",
        height=550,
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            "Rechnungsdatum":  st.column_config.TextColumn("Rechnungsdatum",          width="medium"),
            "Verkäufer":       st.column_config.TextColumn("Verkäufer",               width="medium"),
            "Brutto (€)":      st.column_config.NumberColumn("Brutto (€)",            width="small",  format="%.2f €"),
            "MwSt 19% (€)":    st.column_config.NumberColumn("MwSt 19% (€)",          width="small",  format="%.2f €"),
            "Netto (€)":       st.column_config.NumberColumn("Netto (€)",             width="small",  format="%.2f €"),
            "Zahlart":         st.column_config.SelectboxColumn(
                                   "Zahlart (결제)", options=ZAHLART_OPTIONS,
                                   width="medium", required=True,
                               ),
            "Beleg_Nr":        st.column_config.TextColumn("Beleg_Nr (영수증 번호)",  width="medium"),
            "Verknüpfte_INV":  st.column_config.TextColumn("Verknüpfte_INV (매출번호)", width="medium"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV-Dateiname",         width="max"),
            "_FileExt":        None,
        },
    )

    # ── 다운로드 버튼 ────────────────────────────────────────────────────────
    export_df = st.session_state.edited_receipts.drop(columns=["_FileExt"])
    today     = datetime.now().strftime("%Y%m%d")

    col_dl1, col_dl2 = st.columns(2)

    with col_dl1:
        st.download_button(
            label            = "📥 Excel 다운로드 (.xlsx)",
            data             = build_excel_bytes(export_df),
            file_name        = f"DATEV_Export_{today}.xlsx",
            mime             = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width = True,
        )

    with col_dl2:
        st.download_button(
            label            = "📄 CSV 다운로드 (.csv)",
            data             = export_df.to_csv(index=True, encoding="utf-8-sig"),
            file_name        = f"DATEV_Export_{today}.csv",
            mime             = "text/csv",
            use_container_width = True,
        )

import io
import time
import zipfile
from datetime import datetime
import pandas as pd
import streamlit as st
from pypdf import PdfReader, PdfWriter
from PIL import Image

# 💾 1~2단계: 데이터베이스 및 모델 레이어 연결
from database.connection import init_db, engine
from database.models import Company, Receipt, BankTransaction
from sqlmodel import Session, select

# 🔮 3단계: Structured Output OCR 엔진 연결
from backend.ocr import ask_gemini_structured

# 📊 4~5단계: 비즈니스 세무 계산, 정밀 검증 및 매칭 엔진 연결
from backend.tax import calculate_tax_details
from backend.datev import build_datev_filename, build_excel_bytes
from backend.core.validation import verify_receipt_data
from backend.core.matching import match_transaction_to_receipts

# ══════════════════════════════════════════════════════════════════════════════
# 1. 시스템 초기화 및 인프라 가동
# ══════════════════════════════════════════════════════════════════════════════
PAGE_TITLE, PAGE_ICON = "DATEV AI Accounting Platform", "🧾"
FREE_TIER_DELAY = 4.2
MIME_MAP = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(f"{PAGE_ICON} AI 회계 자동화 플랫폼 Engine")

# SQLite DB 테이블 생성 및 기본 회사(Tenant) 세팅
init_db()
with Session(engine) as db_session:
    default_company = db_session.exec(select(Company).where(Company.name == "Default Company DE")).first()
    if not default_company:
        default_company = Company(name="Default Company DE", industry="E-Commerce", skr_mode="SKR04", vat_type="Standard")
        db_session.add(default_company)
        db_session.commit()
        db_session.refresh(default_company)

# ══════════════════════════════════════════════════════════════════════════════
# 2. 세션 상태 보관소 관리 (드롭다운 버그 완벽 해결용 동적 동기화)
# ══════════════════════════════════════════════════════════════════════════════
if "custom_zahlungswege" not in st.session_state:
    st.session_state.custom_zahlungswege = ["Firmenkonto", "Kreditkarte", "Paypal", "Bar"]
if "edited_receipts" not in st.session_state:
    st.session_state.edited_receipts = None

# API Key 입력 관리
API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    API_KEY = st.text_input("🔑 Gemini API-Key eingeben", type="password")

# ══════════════════════════════════════════════════════════════════════════════
# 3. 데이터 에디터 컨트롤러 (실시간 동적 검증 및 재계산)
# ══════════════════════════════════════════════════════════════════════════════
def on_table_edited() -> None:
    edit_state = st.session_state.get("beleg_editor_key", {})
    edited_rows, deleted_rows = edit_state.get("edited_rows", {}), edit_state.get("deleted_rows", [])
    if not edited_rows and not deleted_rows: return

    df = st.session_state.edited_receipts.copy()
    
    # 행 삭제 처리
    if deleted_rows:
        df = df.drop(index=[df.index[int(idx)] for idx in deleted_rows]).reset_index(drop=True)
        df.index = range(1, len(df) + 1)
        df.index.name = "Nr."
        st.session_state.edited_receipts = df
        return

    # 행 수정 처리
    for row_idx_str, changes in edited_rows.items():
        label = df.index[int(row_idx_str)]
        for col, new_val in changes.items(): 
            df.at[label, col] = new_val

        # 실시간 세금 및 파일명 재계산 로직 엔진 가동
        brutto_eur = float(df.at[label, "Bruttobetrag (EUR)"])
        mwst_19, mwst_7, netto = calculate_tax_details(brutto_eur, str(df.at[label, "Steuerschlüssel"]))
        
        df.at[label, "USt/Vorsteuer 19%"], df.at[label, "Vorsteuer 7%"], df.at[label, "Nettobetrag (Haben)"] = mwst_19, mwst_7, netto
        df.at[label, "Zukünftiger DATEV-Dateiname"] = build_datev_filename(
            str(df.at[label, "Rechnungsdatum"]), str(df.at[label, "Verkäufer"]), brutto_eur,
            str(df.at[label, "Zahlweg (DATEV)"]), str(df.at[label, "Beleg_Nr"]), str(df.at[label, "🔗 Ausgangs-INV"])
        )
        
        # 4단계: 실시간 세무 유효성 정밀 검증 파이프라인 연동
        with Session(engine) as db_session:
            receipt_dict = {
                "vendor": df.at[label, "Verkäufer"], "invoice_number": df.at[label, "Beleg_Nr"],
                "date": df.at[label, "Rechnungsdatum"], "brutto": brutto_eur, "netto": netto,
                "vat19": mwst_19, "vat7": mwst_7, "currency": "EUR"
            }
            is_valid, errs = verify_receipt_data(db_session, default_company.id, receipt_dict)
            df.at[label, "Status/Validation"] = "✅ OK" if is_valid else f"❌ {errs[0]}"

    st.session_state.edited_receipts = df

# ══════════════════════════════════════════════════════════════════════════════
# 4. 파일 업로드 및 비즈니스 로직 파이프라인 가동 (View & Data Loop)
# ══════════════════════════════════════════════════════════════════════════════
# 결제 수단 동적 추가 폼
with st.expander("💳 결제 수단 관리 (Zahlungswege 마스터 데이터)", expanded=False):
    with st.form("zw_form", clear_on_submit=True):
        zw_in = st.text_input("새로운 결제 수단 입력 (예: Corporate_Visa)")
        if st.form_submit_button("마스터 데이터 추가") and zw_in:
            if zw_in not in st.session_state.custom_zahlungswege:
                st.session_state.custom_zahlungswege.append(zw_in)
                st.rerun()

st.markdown("---")
uploaded_files = st.file_uploader("📂 인보이스/영수증 파일 업로드", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

cc1, cc2 = st.columns(2)
default_zahlart = cc1.radio("기본 적용할 결제 수단 선택", options=st.session_state.custom_zahlungswege, index=0, horizontal=True)
selected_skr = cc2.radio("적용할 Kontenrahmen 선택", options=["SKR03", "SKR04"], index=1, horizontal=True)

if uploaded_files:
    # 캐시 락 풀기 방지용 고유 배치 키 조합 생성
    batch_key = "".join(f.name for f in uploaded_files) + f"_{selected_skr}_{default_zahlart}_{len(st.session_state.custom_zahlungswege)}"
    if st.session_state.get("last_batch_key") != batch_key:
        st.session_state.last_batch_key, st.session_state.edited_receipts = batch_key, None

    if st.session_state.get("edited_receipts") is None:
        rows = []
        tot = len(uploaded_files)
        p_bar = st.progress(0)
        
        with Session(engine) as db_session:
            for idx, f in enumerate(uploaded_files):
                f_bytes = f.read()
                ext = f.name.rsplit(".", 1)[-1].lower()
                mime = MIME_MAP.get(ext, "application/octet-stream")
                
                # 🌟 [3단계] Gemini Structured Output JSON 결합 호출
                ai_res = ask_gemini_structured(f_bytes, mime, API_KEY)
                
                # 📊 [4단계] 세금 재계산 모듈 작동
                mwst_19, mwst_7, netto = calculate_tax_details(ai_res.total, ai_res.mwst_type)
                
                # 4단계 검증기 엔진 연동 및 검사
                r_check = {
                    "vendor": ai_res.vendor, "invoice_number": ai_res.beleg_nr, "date": ai_res.datum,
                    "brutto": ai_res.total, "netto": netto, "vat19": mwst_19, "vat7": mwst_7, "currency": ai_res.currency
                }
                is_valid, errs = verify_receipt_data(db_session, default_company.id, r_check)
                val_status = "✅ OK" if is_valid else f"❌ {errs[0]}"
                
                rows.append({
                    "Rechnungsdatum": ai_res.datum, "Verkäufer": ai_res.vendor,
                    "Bruttobetrag (EUR)": ai_res.total, "USt/Vorsteuer 19%": mwst_19, "Vorsteuer 7%": mwst_7, "Nettobetrag (Haben)": netto,
                    "Zahlweg (DATEV)": default_zahlart, "Steuerschlüssel": ai_res.mwst_type, "Beleg_Nr": ai_res.beleg_nr, "🔗 Ausgangs-INV": "",
                    "Status/Validation": val_status,
                    "Zukünftiger DATEV-Dateiname": build_datev_filename(ai_res.datum, ai_res.vendor, ai_res.total, default_zahlart, ai_res.beleg_nr, ""),
                    "_FileExt": ext, "_RawBytes": f_bytes
                })
                p_bar.progress(int((idx + 1) / tot * 100))
                if tot > 1 and idx < tot - 1: time.sleep(FREE_TIER_DELAY)
            
        st.session_state.edited_receipts = pd.DataFrame(rows, index=range(1, len(rows) + 1))
        st.session_state.edited_receipts.index.name = "Nr."

    # 🌟 [버그 원인 제거 핵심] 드롭다운 옵션이 항상 유동적으로 동기화된 데이터 테이블 렌더링
# 🌟 [SKR 항목 노출 및 드롭다운 동기화] 데이터 에디터 렌더링
    st.data_editor(
        st.session_state.edited_receipts, 
        use_container_width=True, 
        num_rows="dynamic", 
        height=350, 
        key="beleg_editor_key", 
        on_change=on_table_edited,
        column_config={
            # 👇 이 부분이 누락되었거나 이름이 안 맞아 숨겨졌던 SKR 항목을 화면에 강제로 등장시킵니다.
            f"{selected_skr}": st.column_config.TextColumn(
                label=f"📊 {selected_skr} 계정", 
                width="medium", 
                help=" Steuerberater가 사용하는 독일 표준 계정 과목 코드입니다."
            ),
            "Rechnungsdatum": st.column_config.TextColumn("Rechnungsdatum"),
            "Verkäufer": st.column_config.TextColumn("Verkäufer"),
            "Bruttobetrag (EUR)": st.column_config.NumberColumn("Bruttobetrag (EUR)", format="%,.2f €"),
            "USt/Vorsteuer 19%": st.column_config.NumberColumn("USt/Vorsteuer 19%", format="%,.2f €"),
            "Vorsteuer 7%": st.column_config.NumberColumn("Vorsteuer 7%", format="%,.2f €"),
            "Nettobetrag (Haben)": st.column_config.NumberColumn("Nettobetrag (Haben)", format="%,.2f €"),
            "Zahlweg (DATEV)": st.column_config.SelectboxColumn("Zahlweg (DATEV)", options=st.session_state.custom_zahlungswege, width="medium"),
            "Steuerschlüssel": st.column_config.SelectboxColumn("Steuerschlüssel", options=["19_Only", "7_Only", "Split", "AUTO_19", "0_Only"]),
            "Status/Validation": st.column_config.TextColumn("검증 상태", disabled=True),
            "Zukünftiger DATEV-Dateiname": st.column_config.TextColumn("DATEV 파일명 명명 규칙", width="max"),
            "_FileExt": None, 
            "_RawBytes": None
        }
    )
    # ══════════════════════════════════════════════════════════════════════════════
    # 5. DB 확정 저장 및 내보내기 파이프라인
    # ══════════════════════════════════════════════════════════════════════════════
    df_final = st.session_state.edited_receipts
    
    if st.button("💾 데이터베이스(PostgreSQL/SQLite) 최종 동기화 및 적재", type="primary", use_container_width=True):
        with Session(engine) as db_session:
            saved_count = 0
            for _, r in df_final.iterrows():
                # 이미 존재하는 인보이스 번호 중복 저장 방지
                exists = db_session.exec(select(Receipt).where(Receipt.invoice_number == str(r["Beleg_Nr"]), Receipt.company_id == default_company.id)).first()
                if not exists:
                    db_receipt = Receipt(
                        company_id=default_company.id, vendor=r["Verkäufer"], invoice_number=r["Beleg_Nr"],
                        date=r["Rechnungsdatum"], brutto=r["Bruttobetrag (EUR)"], netto=r["Nettobetrag (Haben)"],
                        vat19=r["USt/Vorsteuer 19%"], vat7=r["Vorsteuer 7%"], steuerschluessel=r["Steuerschlüssel"]
                    )
                    db_session.add(db_receipt)
                    saved_count += 1
            db_session.commit()
        st.success(f"🎉 총 {saved_count}개의 새로운 영수증 레코드가 로컬 분산 DB 트랜잭션 적재에 성공했습니다!")

    st.markdown("### 📥 익스포트 패키지 빌드")
    dl1, dl2 = st.columns(2)
    dl1.download_button("📊 Excel-Export (회계 감사용)", data=build_excel_bytes(df_final), file_name=f"DATEV_{selected_skr}_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)
    
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        for _, r in df_final.iterrows():
            z.writestr(r["Zukünftiger DATEV-Dateiname"], r["_RawBytes"])
    dl2.download_button("📁 PDF-ZIP 구조화 파일 다운로드", data=zip_buf.getvalue(), file_name=f"DATEV_Belege_{datetime.now().strftime('%Y%m%d')}.zip", use_container_width=True)

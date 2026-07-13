import streamlit as st
from database.connection import init_db, engine
from database.models import Company, Receipt
from sqlmodel import Session, select
from backend.ocr import ask_gemini_structured
from backend.tax import calculate_tax_details
from backend.datev import build_datev_filename

# 1단계 DB 테이블 자동 초기화 및 빌드 실행
init_db()

# 세션 내 가상의 기본 회사(Default Tenant) 확보 로직
with Session(engine) as db_session:
    default_company = db_session.exec(select(Company).where(Company.name == "My Business DE")).first()
    if not default_company:
        default_company = Company(name="My Business DE", industry="Software", skr_mode="SKR04", vat_type="Standard")
        db_session.add(default_company)
        db_session.commit()
        db_session.refresh(default_company)

st.title("🚀 SaaS 준비형 AI 회계 플랫폼 Engine")

# 영수증 파일 업로드 이벤트 발생 시 내부 처리 로직 예시
# (기존 rows.append 영역에 이제 실제 데이터베이스 적재 코드가 매핑됩니다)
if st.checkbox("DB 적재 테스트 파이프라인 가동"):
    # 파일 업로드 및 분석 루프가 돌았다고 가정 시:
    with Session(engine) as db_session:
        # 3단계: 정형화 JSON OCR 호출 결과 수신
        # ai_res: StructuredReceiptResponse = ask_gemini_structured(f_bytes, mime, api_key)
        
        # 가상의 데이터 매핑 예시
        mwst_19, mwst_7, netto = calculate_tax_details(119.0, "AUTO_19")
        
        new_receipt = Receipt(
            company_id=default_company.id,
            vendor="Shell",
            invoice_number="INV-2026-991",
            date="2026-07-13",
            brutto=119.0,
            netto=netto,
            vat19=mwst_19,
            vat7=mwst_7,
            currency="EUR",
            steuerschluessel="AUTO_19"
        )
        
        db_session.add(new_receipt)
        db_session.commit() # 💾 하드디스크 accounting.db에 영구 보존 처리 완료!
        st.success("데이터베이스에 영수증 정보가 성공적으로 영구 적재되었습니다.")

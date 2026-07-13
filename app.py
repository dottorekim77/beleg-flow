import streamlit as st
from sqlmodel import Session
from database.connection import engine
from database.models import BankTransaction
from backend.core.validation import verify_receipt_data
from backend.core.matching import match_transaction_to_receipts, execute_auto_matching

st.subheader("⚙️ 백엔드 코어 비즈니스 로직 테스트 벤치")

with Session(engine) as db_session:
    # 예시 데이터 생성 및 유효성 검증 시뮬레이션
    mock_receipt_input = {
        "vendor": "Amazon EU",
        "invoice_number": "INV-2026-005",
        "date": "2026-07-12",
        "brutto": 23.45,
        "netto": 19.71,
        "vat19": 3.74,
        "vat7": 0.0,
        "currency": "EUR"
    }
    
    # 4단계 검증기 작동
    is_valid, err_messages = verify_receipt_data(db_session, company_id=1, r_dict=mock_receipt_input)
    
    if not is_valid:
        st.error(f"❌ 검증 실패: {err_messages}")
    else:
        st.success("✅ 4단계 통과: 데이터 정밀 검증 및 중복 없음 확인 완료.")
        
        # 5단계 매칭 엔진 작동 테스트용 가상 은행 거래 데이터 적재
        mock_tx = BankTransaction(
            booking_date="2026-07-13", # 하루 뒤 대금 인출 시나리오
            amount=-23.45,
            payee="AMAZON DE PAYMENTS"
        )
        
        # 매칭 알고리즘 가동
        candidates = match_transaction_to_receipts(db_session, mock_tx, company_id=1)
        
        if candidates:
            best = candidates[0]
            st.write(f"🎯 매칭 후보 발견! 점수: **{best['score']}점** | 상태: `{best['confidence']}`")
            st.caption(f"영수증 판매처: {best['receipt'].vendor} ↔ 은행 공급처: {mock_tx.payee}")

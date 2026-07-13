import re
from datetime import datetime
from sqlmodel import Session, select
from database.models import Receipt

def validate_iso_date(date_str: str) -> bool:
    """YYYY-MM-DD 형식 및 실제 존재하는 날짜인지 검증"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def validate_tax_math(brutto: float, netto: float, vat19: float, vat7: float, tolerance: float = 0.05) -> bool:
    """
    Netto + VAT = Brutto 공식 검증
    floating point 오차 및 rounding 문제를 고려하여 일정 허용 범위(tolerance) 내에 있는지 체크
    """
    calculated_brutto = netto + vat19 + vat7
    return abs(brutto - calculated_brutto) <= tolerance

def check_duplicate_invoice(db: Session, company_id: int, vendor: str, invoice_number: str, brutto: float) -> bool:
    """
    동일 회사 내 중복 영수증 검사
    인보이스 번호가 같거나, 번호가 없더라도 (거래처, 금액)이 완벽히 일치하면 중복으로 판단
    """
    # 1. 인보이스 번호 기반 검사
    if invoice_number and invoice_number.lower() != "none":
        stmt = select(Receipt).where(
            Receipt.company_id == company_id,
            Receipt.invoice_number == invoice_number,
            Receipt.vendor == vendor
        )
        if db.exec(stmt).first():
            return True

    # 2. 번호가 없을 경우 금액 및 거래처 기반 유사 중복 검사
    stmt_fallback = select(Receipt).where(
        Receipt.company_id == company_id,
        Receipt.vendor == vendor,
        Receipt.brutto == brutto
    )
    if db.exec(stmt_fallback).first():
        return True

    return False

def verify_receipt_data(db: Session, company_id: int, r_dict: dict) -> tuple[bool, list[str]]:
    """
    영수증 전체 통합 유효성 검사 매니저
    Returns: (Pass 여부, 에러 메시지 리스트)
    """
    errors = []
    
    # 1. 금액 음수 여부 검사
    if r_dict.get("brutto", 0) <= 0:
        errors.append("Bruttobetrag muss größer als 0 sein.")
        
    # 2. 날짜 유효성 검사
    if not validate_iso_date(r_dict.get("date", "")):
        errors.append("Ungültiges Datumsformat. Muss YYYY-MM-DD sein.")
        
    # 3. 통화 검사
    if r_dict.get("currency", "EUR") not in ["EUR", "USD"]:
        errors.append("Unterstützte Währungen sind nur EUR und USD.")
        
    # 4. 세금 합계 산술 검증
    if not validate_tax_math(r_dict.get("brutto", 0), r_dict.get("netto", 0), r_dict.get("vat19", 0), r_dict.get("vat7", 0)):
        errors.append("Mathematischer Fehler: Netto + MwSt stimmt nicht mit Brutto überein.")
        
    # 5. 중복 인보이스 검사
    if check_duplicate_invoice(db, company_id, r_dict.get("vendor", ""), r_dict.get("invoice_number", ""), r_dict.get("brutto", 0)):
        errors.append("Duplikat-Warnung: Dieser Beleg existiert bereits im System.")
        
    return len(errors) == 0, errors

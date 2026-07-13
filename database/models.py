from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

# 🌟 [핵심] Streamlit 재실행 시 테이블 중복 등록 에러(InvalidRequestError)를 완전히 방지하는 치트키
# 이미 메타데이터에 등록된 테이블명이 있다면 초기화하여 충돌을 막습니다.
def clear_metadata_cache(table_name: str):
    if table_name in SQLModel.metadata.tables:
        del SQLModel.metadata.tables[table_name]

# ══════════════════════════════════════════════════════════════════════════════
# 1. Company 모델 (회사 정보 마스터)
# ══════════════════════════════════════════════════════════════════════════════
clear_metadata_cache("companies")
class Company(SQLModel, table=True):
    __tablename__ = "companies"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    industry: Optional[str] = None
    skr_mode: str = Field(default="SKR04")
    vat_type: str = Field(default="Standard")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Receipt 모델 (비용 / 영수증 마스터)
# ══════════════════════════════════════════════════════════════════════════════
clear_metadata_cache("receipts")
class Receipt(SQLModel, table=True):
    __tablename__ = "receipts"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    vendor: str = Field(index=True)
    invoice_number: Optional[str] = Field(default="None", index=True)
    date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    brutto: float = Field(default=0.0)
    netto: float = Field(default=0.0)
    vat19: float = Field(default=0.0)
    vat7: float = Field(default=0.0)
    skr_account: Optional[str] = None
    ocr_confidence: float = Field(default=1.0)
    file_path: Optional[str] = None
    currency: str = Field(default="EUR")
    steuerschluessel: str = Field(default="AUTO_19")


# ══════════════════════════════════════════════════════════════════════════════
# 3. BankTransaction 모델 (은행 거래 정보)
# ══════════════════════════════════════════════════════════════════════════════
clear_metadata_cache("bank_transactions")
class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    booking_date: str
    amount: float
    payee: str
    zweck: Optional[str] = Field(default="")
    matched_receipt_id: Optional[int] = Field(default=None, index=True)
    match_score: float = Field(default=0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. LearningRule 모델 (AI 추천 엔진용 히스토리)
# ══════════════════════════════════════════════════════════════════════════════
clear_metadata_cache("learning_rules")
class LearningRule(SQLModel, table=True):
    __tablename__ = "learning_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    vendor: str = Field(index=True)
    item_keyword: Optional[str] = Field(default="", index=True)
    skr_account: str
    count: int = Field(default=1)

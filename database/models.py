from typing import Optional
from datetime import datetime
from sqlalchemy import MetaData
from sqlmodel import SQLModel, Field

# 🌟 Streamlit Hot-Reload 충돌 방지용 독립 메타데이터 인스턴스 생성
# 기본 글로벌 SQLModel.metadata를 사용하지 않고 독립된 생태계를 구성합니다.
custom_metadata = MetaData()

# ══════════════════════════════════════════════════════════════════════════════
# 1. Company 모델 (회사 정보 마스터)
# ══════════════════════════════════════════════════════════════════════════════
class Company(SQLModel, table=True):
    __tablename__ = "companies"
    metadata = custom_metadata  
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    industry: Optional[str] = None
    skr_mode: str = Field(default="SKR04")
    vat_type: str = Field(default="Standard")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Receipt 모델 (비용 / 영수증 마스터)
# ══════════════════════════════════════════════════════════════════════════════
class Receipt(SQLModel, table=True):
    __tablename__ = "receipts"
    metadata = custom_metadata  

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
class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"
    metadata = custom_metadata  

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
class LearningRule(SQLModel, table=True):
    __tablename__ = "learning_rules"
    metadata = custom_metadata  

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    vendor: str = Field(index=True)
    item_keyword: Optional[str] = Field(default="", index=True)
    skr_account: str
    count: int = Field(default=1)

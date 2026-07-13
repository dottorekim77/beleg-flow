from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship

# ══════════════════════════════════════════════════════════════════════════════
# 1. Company 모델 (계정/회사 정보 마스터)
# ══════════════════════════════════════════════════════════════════════════════
class Company(SQLModel, table=True):
    __tablename__ = "companies"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    industry: Optional[str] = None
    skr_mode: str = Field(default="SKR04")  # SKR03 또는 SKR04
    vat_type: str = Field(default="Standard")  # Standard 또는 Kleinunternehmer

    # 양방향 관계 매핑 (String 기반으로 오버랩 충돌 방지)
    receipts: List["Receipt"] = Relationship(back_populates="company")
    rules: List["LearningRule"] = Relationship(back_populates="company")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Receipt 모델 (비용/매출 인보이스)
# ══════════════════════════════════════════════════════════════════════════════
class Receipt(SQLModel, table=True):
    __tablename__ = "receipts"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
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

    # 상위 회사와의 관계
    company: "Company" = Relationship(back_populates="receipts")
    
    # 은행 매칭 테이블과의 일대다 관계
    bank_matches: List["BankTransaction"] = Relationship(back_populates="matched_receipt")


# ══════════════════════════════════════════════════════════════════════════════
# 3. BankTransaction 모델 (은행 계좌 내역 통계)
# ══════════════════════════════════════════════════════════════════════════════
class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    booking_date: str
    amount: float
    payee: str
    zweck: Optional[str] = Field(default="")
    matched_receipt_id: Optional[int] = Field(default=None, foreign_key="receipts.id")
    match_score: float = Field(default=0.0)

    # 매칭된 영수증과의 관계
    matched_receipt: Optional["Receipt"] = Relationship(back_populates="bank_matches")


# ══════════════════════════════════════════════════════════════════════════════
# 4. LearningRule 모델 (AI 피드백 피팅 시스템 마스터)
# ══════════════════════════════════════════════════════════════════════════════
class LearningRule(SQLModel, table=True):
    __tablename__ = "learning_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    vendor: str = Field(index=True)
    item_keyword: Optional[str] = Field(default="", index=True)
    skr_account: str
    count: int = Field(default=1)

    # 상위 회사와의 관계
    company: "Company" = Relationship(back_populates="rules")

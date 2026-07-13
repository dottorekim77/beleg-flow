from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship

class Company(SQLModel, table=True):
    __tablename__ = "companies"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    industry: Optional[str] = None
    skr_mode: str = Field(default="SKR04")  # SKR03 또는 SKR04
    vat_type: str = Field(default="Standard")  # Standard 또는 Kleinunternehmer

    # 관계 정의
    receipts: List["Receipt"] = Relationship(back_populates="company")
    rules: List["LearningRule"] = Relationship(back_populates="company")


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

    # 관계 정의
    company: Company = Relationship(back_populates="receipts")
    bank_matches: List["BankTransaction"] = Relationship(back_populates="matched_receipt")


class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    booking_date: str
    amount: float
    payee: str
    matched_receipt_id: Optional[int] = Field(default=None, foreign_key="receipts.id")
    match_score: float = Field(default=0.0)

    # 관계 정의
    matched_receipt: Optional[Receipt] = Relationship(back_populates="bank_matches")


class LearningRule(SQLModel, table=True):
    __tablename__ = "learning_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    vendor: str = Field(index=True)
    item_keyword: Optional[str] = Field(default="", index=True)
    skr_account: str
    count: int = Field(default=1)

    # 관계 정의
    company: Company = Relationship(back_populates="rules")

import os
from sqlmodel import SQLModel, create_engine, Session

# 💡 추후 PostgreSQL 전환 시 환경변수만 바꾸면 끝!
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./accounting.db")

# SQLite의 경우 멀티스레드 환경 대응을 위해 connect_args 추가
engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

def init_db():
    """앱 구동 시 테이블 자동 생성"""
    SQLModel.metadata.create_entropy = True
    SQLModel.metadata.create_all(engine)

def get_db():
    """FastAPI 및 Streamlit에서 사용할 DB 세션 제너레이터"""
    with Session(engine) as session:
        yield session

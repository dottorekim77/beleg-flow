import os
from sqlmodel import create_engine, Session

# 💡 추후 PostgreSQL 전환 시 환경변수만 바꾸면 끝!
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./accounting.db")

# SQLite의 경우 멀티스레드 환경 대응을 위해 connect_args 추가
engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

def init_db():
    """
    앱 구동 시 테이블 자동 생성 (Hot-Reload 및 순환 참조 방지 버전)
    """
    # 1. 순환 참조를 막기 위해 함수 내부에서 로컬 임포트 수행
    from database.models import custom_metadata
    
    # 2. 오직 격리된 custom_metadata만을 이용하여 데이터베이스에 테이블을 빌드합니다.
    custom_metadata.create_all(engine)

def get_db():
    """FastAPI 및 Streamlit에서 사용할 DB 세션 제너레이터"""
    with Session(engine) as session:
        yield session

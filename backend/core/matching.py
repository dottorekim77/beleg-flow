from datetime import datetime
from difflib import SequenceMatcher
from sqlmodel import Session, select
from database.models import Receipt, BankTransaction

def calculate_string_similarity(str1: str, str2: str) -> float:
    """두 문자열(판매처 이름 vs 은행 적요/파트너명)의 유사도를 0.0 ~ 1.0 사이로 반환"""
    if not str1 or not str2:
        return 0.0
    s1, s2 = str1.lower().strip(), str2.lower().strip()
    # 부분 일치 대응 (e.g., 'Amazon EU'와 'Amazon')
    if s1 in s2 or s2 in s1:
        return 0.95
    return SequenceMatcher(None, s1, s2).ratio()

def calculate_date_score(date_str1: str, date_str2: str) -> float:
    """
    날짜 차이에 따른 점수 계산 (최대 ±3일 허용)
    당일 일치: 1.0, 1일 차이: 0.7, 2일 차이: 0.4, 3일 차이: 0.1, 4일 이상: 0.0
    """
    try:
        d1 = datetime.strptime(date_str1, "%Y-%m-%d")
        d2 = datetime.strptime(date_str2, "%Y-%m-%d")
        diff_days = abs((d1 - d2).days)
        
        if diff_days == 0: return 1.0
        elif diff_days == 1: return 0.7
        elif diff_days == 2: return 0.4
        elif diff_days == 3: return 0.1
        else: return 0.0
    except ValueError:
        return 0.0

def match_transaction_to_receipts(db: Session, tx: BankTransaction, company_id: int) -> list[dict]:
    """
    하나의 은행 거래 내역에 대해 매칭 가능한 영수증 후보들을 점수 순으로 나열
    가중치 반영: 금액(60%) + 날짜(25%) + 판매처(15%)
    """
    # 해당 회사의 아직 매칭되지 않은 영수증 전체 조회
    # (실제 서비스에서는 성능을 위해 최근 3개월 데이터만 필터링하는 것이 좋습니다)
    stmt = select(Receipt).where(Receipt.company_id == company_id)
    all_receipts = db.exec(stmt).all()
    
    match_candidates = []
    
    # 은행 지출액은 음수로 들어오므로 절대값 처리
    tx_amount = abs(tx.amount)
    
    for receipt in all_receipts:
        # 1. 금액 점수 (60%): 완벽 일치 시 1.0, 불일치 시 0.0 (금융 매칭의 대전제)
        # 단, 소수점 오차를 감안하여 0.01유로 미만 차이는 일치로 인정
        amt_score = 1.0 if abs(receipt.brutto - tx_amount) < 0.01 else 0.0
        
        # 2. 날짜 점수 (25%): 영수증 날짜와 은행 대금 결제일 비교
        date_score = calculate_date_score(receipt.date, tx.booking_date)
        
        # 3. 판매처 유사도 점수 (15%): 영수증 Vendor 명과 은행 Payee/Verwendungszweck 비교
        vendor_sim = max(
            calculate_string_similarity(receipt.vendor, tx.payee),
            calculate_string_similarity(receipt.vendor, tx.zweck if hasattr(tx, 'zweck') else "")
        )
        
        # 🔥 총합 가중치 스코어 산출 (100점 만점 기준)
        total_score = (amt_score * 60.0) + (date_score * 25.0) + (vendor_sim * 15.0)
        
        # 최소 조건: 금액이 아예 안 맞거나 총점이 60점 미만(금액만 맞고 날짜/이름이 아예 다른 경우)은 후보군 제외
        if amt_score > 0 and total_score >= 60.0:
            match_candidates.append({
                "receipt": receipt,
                "score": round(total_score, 2),
                "confidence": "HIGH" if total_score >= 95.0 else "MANUAL_CHECK"
            })
            
    # 점수가 높은 순으로 정렬하여 반환
    match_candidates.sort(key=lambda x: x["score"], reverse=True)
    return match_candidates

def execute_auto_matching(db: Session, company_id: int) -> int:
    """
    배치 프로세스용 엔진: 미매칭 은행 거래들을 순회하며 95점 이상은 자동 확정 처리
    """
    stmt = select(BankTransaction).where(BankTransaction.matched_receipt_id == None)
    unmatched_txs = db.exec(stmt).all()
    
    auto_matched_count = 0
    
    for tx in unmatched_txs:
        candidates = match_transaction_to_receipts(db, tx, company_id)
        if candidates and candidates[0]["score"] >= 95.0:
            best_receipt = candidates[0]["receipt"]
            
            # DB 관계 맵핑 및 스코어 업데이트
            tx.matched_receipt_id = best_receipt.id
            tx.match_score = candidates[0]["score"]
            db.add(tx)
            
            auto_matched_count += 1
            
    db.commit()
    return auto_matched_count

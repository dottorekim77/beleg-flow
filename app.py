import os
import re
from datetime import datetime
import io
import pandas as pd
from pypdf import PdfReader
import streamlit as st

# 1. 스트림릿 웹페이지 기본 설정
st.set_page_config(page_title="독일 영수증 자동 정산기", page_icon="🧾", layout="centered")

st.title("🧾 독일 영수증 자동 정산 & MwSt 계산기")
st.write("영수증 PDF 파일들을 아래에 한 번에 드래그 앤 드롭 하세요. 파일명 변경 및 19% 부가세를 자동으로 정산해 드립니다.")

# --- 데이터 처리 핵심 함수들 ---
def extract_text_from_pdf(file_bytes):
    """업로드된 파일 바이츠에서 텍스트 추출"""
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        return ""

def parse_receipt_info(text):
    """텍스트에서 날짜, 금액, 발행처, 19% 부가세 추출"""
    # 날짜 추출
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")
    if "." in date_str:
        try: date_str = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: pass

    # 발행처 분류
    vendor = "Unknown"
    if "Amazon" in text or "AMAZON" in text: vendor = "Amazon"
    elif "Tesla" in text or "Supercharger" in text: vendor = "Tesla"
    elif "Santander" in text: vendor = "Santander"
    elif "stadtmobil" in text: vendor = "Stadtmobil"

    # 금액 추출
    total_match = re.search(r"(Total|Gesamtsumme|Endbetrag|EUR|€)\s*:?\s*([\d\.]+,\d{2})", text, re.IGNORECASE)
    total_amount = float(total_match.group(2).replace(".", "").replace(",", ".")) if total_match else 0.0

    # 19% 부가세 계산
    mwst_match = re.search(r"(19%\s*(MwSt|USt|VAT)|MwSt\s*19%)\s*:?\s*([\d\.]+,\d{2})", text, re.IGNORECASE)
    if mwst_match:
        mwst_19 = float(mwst_match.group(3).replace(".", "").replace(",", "."))
    else:
        mwst_19 = round(total_amount * 19 / 119, 2) if "19%" in text and total_amount > 0 else 0.0

    return date_str, vendor, total_amount, mwst_19

# --- UI 구현 부 ---

# 2. 다중 파일 업로드 창 (Streamlit의 가장 강력한 기능)
uploaded_files = st.file_uploader("영수증 PDF 파일을 선택하세요 (여러 개 가능)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    
    st.subheader("정산 진행 내역")
    
    # 각 파일별로 돌면서 분석
    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        text = extract_text_from_pdf(file_bytes)
        date_str, vendor, total, mwst_19 = parse_receipt_info(text)
        
        # DATEV 양식의 새 파일명 제안
        proposed_name = f"{date_str}_{vendor}_{total:.2f}EUR.pdf"
        
        st.success(f"✔ 분석 완료: {uploaded_file.name} ➔ **{proposed_name}**")
        
        receipt_data.append({
            "Datum (날짜)": date_str,
            "Firma (발행처)": vendor,
            "Brutto (총금액)": total,
            "MwSt 19% (부가세)": mwst_19,
            "Netto (공급가액)": round(total - mwst_19, 2),
            "추천 파일명": proposed_name
        })
        
    # 3. 데이터프레임 변환 및 화면 표시
    df = pd.DataFrame(receipt_data)
    
    st.markdown("---")
    st.subheader("📊 1개월 사용 내역 요약 통계")
    
    # 합계 계산
    total_brutto = df["Brutto (총금액)"].sum()
    total_mwst = df["MwSt 19% (부가세)"].sum()
    total_netto = df["Netto (공급가액)"].sum()
    
    # 대시보드 상단에 큼직하게 메트릭 표시
    col1, col2, col3 = st.columns(3)
    col1.metric("총 지출 (Brutto)", f"{total_brutto:,.2f} €")
    col2.metric("환급받을 부가세 (MwSt 19%)", f"{total_mwst:,.2f} €")
    col3.metric("순수 비용 (Netto)", f"{total_netto:,.2f} €")
    
    # 결과 표 보여주기
    st.dataframe(df, use_container_width=True)
    
    # 4. 엑셀 파일로 다운로드 기능 추가
    # 합계 행 추가
    total_row = {"Datum (날짜)": "합계 (Total)", "Firma (발행처)": "", "Brutto (총금액)": total_brutto, "MwSt 19% (부가세)": total_mwst, "Netto (공급가액)": total_netto, "추천 파일명": ""}
    df_excel = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    
    # 메모리 상에서 엑셀 파일 생성 (Streamlit Cloud 배포용)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_excel.to_excel(writer, index=False, sheet_name='Ausgaben')
    processed_data = output.getvalue()
    
    current_month = datetime.now().strftime("%Y-%m")
    st.download_button(
        label="📥 정산 내역 엑셀 파일(.xlsx) 다운로드",
        data=processed_data,
        file_name=f"Ausgaben_Report_{current_month}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

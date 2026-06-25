import streamlit as st
from pypdf import PdfReader
import io
import re
from datetime import datetime

st.set_page_config(page_title="Datum Tester", layout="centered")
st.title("🔍 Rechnungsdatum 정확도 테스트")
st.write("PDF를 올리면 내부에서 감지된 모든 날짜와, 최종 선택된 Rechnungsdatum을 보여줍니다.")

def extract_text_from_pdf(file_bytes):
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        return f"Error reading PDF: {e}"

def advanced_date_parser(text):
    text_lines = text.split('\n')
    
    # 1단계: 독일어 Rechnungsdatum 관련 핵심 키워드 주변 집중 탐색
    # (예: Rechnungsdatum: 25.06.2026, Datum v. 12.05.2026 등)
    date_keywords = ["rechnungsdatum", "leistungsdatum", "belegdatum", "datum vom", "datum:", "ausstellungsdatum"]
    
    for line in text_lines:
        line_low = line.lower()
        if any(kw in line_low for kw in date_keywords):
            # 키워드가 있는 라인에서 독일식(DD.MM.YYYY) 또는 ISO(YYYY-MM-DD) 날짜 패턴 검색
            match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", line)
            if match:
                return match.group(1), f"정밀 매칭 성공 (키워드 라인: '{line.strip()}')"

    # 2단계: 키워드 매칭 실패 시, 텍스트 전체에서 등장하는 모든 날짜 후보군 수집
    all_dates = re.findall(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    
    if all_dates:
        # 보통 영수증 발행일은 문서 상단(텍스트 처음)에 나올 확률이 높으므로 첫 번째 날짜 선택
        return all_dates[0], f"일반 매칭 (텍스트 내 첫 번째 날짜, 총 {len(all_dates)}개 발견)"

    # 3단계: 아무 날짜도 못 찾은 경우 기본값
    return "0000-00-00", "날짜 패턴을 전혀 찾지 못함"

uploaded_files = st.file_uploader("테스트할 PDF 영수증을 올려주세요", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.markdown(f"### 📄 파일명: {uploaded_file.name}")
        
        # 텍스트 추출
        raw_text = extract_text_from_pdf(uploaded_file.read())
        
        # 날짜 추출 로직 가동
        detected_date, debug_msg = advanced_date_parser(raw_text)
        
        # 독일식 날짜 표준화
        final_date = detected_date
        if "." in detected_date:
            try:
                final_date = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
            except:
                pass
                
        # 결과 출력
        st.info(f"**최종 추출된 날짜:** `{final_date}`")
        st.caption(f"**진단 메시지:** {debug_msg}")
        
        # 텍스트가 어떻게 추출되었는지 직접 눈으로 확인하기 위한 디버깅 창
        with st.expander("이 PDF에서 추출된 실제 텍스트 원본 보기"):
            st.text(raw_text)
        st.markdown("---")

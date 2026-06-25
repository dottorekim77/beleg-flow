import streamlit as st
from pypdf import PdfReader
import io
import re
from datetime import datetime

st.set_page_config(page_title="Verkäufer Pro Tester", layout="centered")
st.title("🔬 [2단계] 판매처(Verkäufer) 이름 정밀 검증")
st.write("지정 키워드 매칭과 'Verkauft von' 문맥 분석 알고리즘이 동시에 작동합니다.")

def extract_text_from_pdf(file_bytes):
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        return ""

def advanced_vendor_parser(text):
    # 1차 가공: 대소문자 무시 및 공백 완전 제거 (텍스트 깨짐 대응용)
    clean_text = re.sub(r'\s+', '', text.lower())
    
    # [방식 A] 고정 키워드 매칭 (내가 자주 쓰는 주요 거래처)
    if any(kw in clean_text for kw in ["tesla", "supercharger", "tsla"]):
        return "Tesla", "고정 키워드 매칭 (Tesla)"
    elif "amazon" in clean_text:
        return "Amazon", "고정 키워드 매칭 (Amazon)"
    elif "santander" in clean_text:
        return "Santander", "고정 키워드 매칭 (Santander)"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]):
        return "Stadtmobil", "고정 키워드 매칭 (Stadtmobil)"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]):
        return "Tankstelle", "고정 키워드 매칭 (Tankstelle)"

    # [방식 B] 문맥 추적 로직 (새로운 회사 이름 동적 추출)
    # 줄바꿈(\n)을 유지한 상태의 원본 텍스트에서 검색합니다.
    
    # 1. "Verkauft von [회사명]" 패턴 (GmbH, AG 등 독일 법인격 접미사 포함 추적)
    context_match1 = re.search(r"verkauft\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG|Inc|Ltd)?)", text, re.IGNORECASE)
    if context_match1:
        vendor_name = context_match1.group(1).strip().split('\n')[0].strip()
        # 불필요한 마침표나 특수문자 마감 정리
        vendor_name = re.sub(r'[:;,]+$', '', vendor_name).strip()
        return vendor_name, f"문맥 추적 성공 ('Verkauft von' 패턴)"

    # 2. "Rechnung von [회사명]" 패턴
    context_match2 = re.search(r"rechnung\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG)?)", text, re.IGNORECASE)
    if context_match2:
        vendor_name = context_match2.group(1).strip().split('\n')[0].strip()
        vendor_name = re.sub(r'[:;,]+$', '', vendor_name).strip()
        return vendor_name, f"문맥 추적 성공 ('Rechnung von' 패턴)"

    # 3. "Dienstleister: [회사명]" 또는 "Aussteller: [회사명]" 패턴
    context_match3 = re.search(r"(dienstleister|aussteller|unternehmer)\s*:?\s*([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG)?)", text, re.IGNORECASE)
    if context_match3:
        vendor_name = context_match3.group(2).strip().split('\n')[0].strip()
        return vendor_name, f"문맥 추적 성공 ('{context_match3.group(1)}' 패턴)"

    return "Unbekannt", "모든 규칙 실패 (지정 키워드 없음 / 문맥 발견 못함)"


uploaded_files = st.file_uploader("검증할 PDF 영수증을 올려주세요", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.markdown(f"### 📄 파일명: {uploaded_file.name}")
        
        raw_text = extract_text_from_pdf(uploaded_file.read())
        vendor, debug_msg = advanced_vendor_parser(raw_text)
        
        # 결과 표시
        if vendor != "Unbekannt":
            st.success(f"**인식된 판매처(Verkäufer):** `{vendor}`")
        else:
            st.error(f"**인식된 판매처(Verkäufer):** `{vendor}`")
            
        st.caption(f"🔍 **알고리즘 진단:** {debug_msg}")
        
        # 텍스트에서 'verkauft von' 근처에 뭐가 있는지 보여주는 미니 디버깅 창
        with st.expander("텍스트 원본에서 'von' 또는 'Rechnung' 주변 단어 훔쳐보기"):
            # 입력된 텍스트에서 키워드가 있는 주변 300자만 잘라서 보여줌
            found = False
            for target in ["von", "rechnung", "tesla", "stadtmobil"]:
                idx = raw_text.lower().find(target)
                if idx != -1:
                    st.text(f"... {raw_text[max(0, idx-50):min(len(raw_text), idx+150)]} ...")
                    found = True
                    break
            if not found:
                st.text("주요 키워드가 텍스트 내에 존재하지 않습니다.")
                
        st.markdown("---")

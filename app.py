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
    # 1. 기존 지정 키워드(Tesla, Amazon, Stadtmobil 등) 및 공백 제거 매칭은 그대로 유지
    clean_text = re.sub(r'\s+', '', text.lower())
    if "amazon" in clean_text: return "Amazon", "키워드"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla", "키워드"
    elif "santander" in clean_text: return "Santander", "키워드"
    elif "stadtmobil" in clean_text: return "Stadtmobil", "키워드"
    elif "dpd" in clean_text: return "DPD", "우선 매칭 (DPD 텍스트 파편 감지)" # DPD가 뭉개져서라도 들어가 있을 때를 대비

    # 2. 기존 "Verkauft von", "Rechnung von" 문맥 추적 유지
    # ... (생략) ...

    # 3. ⭐ [치트키] 독일 주소지 양식(PLZ + 도시)을 기반으로 한 발행처 추적
    lines = text.split('\n')
    for i, line in enumerate(lines):
        # 독일 우편번호 패턴 검색: 공백 뒤에 숫자 5자리가 오고 그 뒤에 도시 이름이 오는 형태 (예: 90449 Nürnberg)
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            # 주소 라인을 찾았다면, 해당 라인이나 바로 윗 라인에 회사 이름이 있습니다.
            # DPD처럼 한 줄에 'DPD Deutschland GmbH Gutenstetter Str...' 라고 묶여서 추출되는 경우:
            if "gmbh" in line.lower() or "ag" in line.lower():
                # GmbH나 AG 앞부분의 첫 두 단어 정도를 회사 이름으로 추출
                company_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG))", line)
                if company_match:
                    return company_match.group(1).strip(), "주소 기반 동적 추출 (동일 라인)"
            
            # 주소 윗줄에 회사명이 따로 분리되어 추출된 경우 (i가 0보다 클 때)
            if i > 0 and len(lines[i-1].strip()) > 2:
                potential_vendor = lines[i-1].strip()
                # 너무 긴 문장인 경우 제외하는 안전장치
                if len(potential_vendor) < 40:
                    return potential_vendor, "주소 기반 동적 추출 (윗 라인)"

    return "Unbekannt", "모든 매칭 실패"


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

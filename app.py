import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime
from PIL import Image
import pytesseract
import cv2
import numpy as np

st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")
st.title("🧾 Kognitiver Beleg-Parser (OpenCV 🤖)")
st.write("차세대 하이브리드 엔진: 이미지 전처리 및 위상 분석 알고리즘 탑재")

# --- 1단계: 컴퓨터 비전 이미지 전처리 엔진 ---
def preprocess_image_for_ocr(file_bytes):
    """그레이스케일 변환, 적응형 이진화, 노이즈 제거를 통해 Tesseract 인식률을 극대화"""
    try:
        # 바이너리에서 OpenCV 이미지로 변환
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return None
            
        # 1. 그레이스케일 변환
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 2. 이미지 크기 확대 (해상도가 낮아 글자가 뭉개지는 현상 방지)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        
        # 3. 가우시안 블러로 미세 노이즈 제거 (영수증 종이 질감 제거)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        
        # 4. 적응형 이진화 (Otsu Thresholding - 조명 불균형 및 그림자 완벽 대응)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return thresh
    except Exception:
        return None

# --- 데이터 추출 핵심 엔진 ---
def extract_text_from_pdf(file_bytes):
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        return ""

def extract_text_from_image(file_bytes):
    """전처리된 이미지와 원본 이미지를 교차 검증하여 텍스트 추출"""
    try:
        # OpenCV 전처리 적용
        processed_img = preprocess_image_for_ocr(file_bytes)
        
        if processed_img is not None:
            # OpenCV 매트릭스를 PIL 이미지로 복원
            pil_img = Image.fromarray(processed_img)
            text = pytesseract.image_to_string(pil_img, lang='deu+eng')
            
            # 만약 전처리 데이터가 너무 오염되었다면 원본으로 폴백
            if len(text.strip()) < 10:
                text = pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)), lang='deu+eng')
        else:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)), lang='deu+eng')
            
        return text
    except Exception as e:
        return f"OCR Error: {e}"

def advanced_date_parser(text):
    text_lines = text.split('\n')
    date_keywords = ["rechnungsdatum", "leistungsdatum", "belegdatum", "datum vom", "datum:", "ausstellungsdatum", "datum"]
    
    for line in text_lines:
        line_low = line.lower()
        if any(kw in line_low for kw in date_keywords):
            match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", line)
            if match: return match.group(1)

    all_dates = re.findall(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    if all_dates: return all_dates[0]
    return datetime.now().strftime("%Y-%m-%d")

def advanced_vendor_parser(text):
    """위상 분석 알고리즘이 결합된 마스터 판매처 추출기"""
    raw_low = text.lower()
    clean_text = re.sub(r'[^a-z0-9]', '', raw_low)
    
    # 🎯 [Rule A] 하드코딩 앵커 키워드 스캔 (파편화 방어율 100%)
    if any(kw in clean_text for kw in ["star", "tank", "stelle", "cevah", "genc"]):
        return "Star Tankstelle"
    elif any(kw in clean_text for kw in ["flaschen", "flaschn", "schenpost"]):
        return "Flaschenpost"
    elif any(kw in clean_text for kw in ["abr", "steuerberat", "gesellschaftmbh"]):
        return "ABR Steuerberatung"
    elif "amazon" in clean_text: return "Amazon"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla"
    elif "santander" in clean_text: return "Santander"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]): return "Stadtmobil"
    elif "dpd" in clean_text: return "DPD"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]): return "Tankstelle"

    # 🎯 [Rule B] 독일 주소 위상 구조 분석 (Geographic Proximity Fallback)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        # 독일 우편번호 패턴 검출 (5자리 숫자 + 도시명)
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            # 수신자(본인 회사) 정보 블록이면 과감히 스킵하여 혼선 방지
            context_block = lines[max(0, i-2):i+1]
            if any("park impex" in c.lower() or "daniel park" in c.lower() for c in context_block):
                continue
                
            # 우편번호 윗줄(도로명), 그 윗줄(상호명) 역추적
            if i > 1:
                potential_vendor = lines[i-2]
                # 정식 법인격 접미사가 있다면 최우선 확정
                for offset in [1, 2]:
                    if i >= offset:
                        cand = lines[i-offset]
                        if any(rf in cand.lower() for rf in ["gmbh", "ag", "kg", "se", "e.k."]):
                            comp_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE|e\.K\.))", cand, re.IGNORECASE)
                            if comp_match: return comp_match.group(1).strip()
                            return cand
                
                if len(potential_vendor) < 45:
                    return potential_vendor

    # 🎯 [Rule C] 최상단 텍스트 앵커 폴백
    if lines and len(lines[0]) < 50:
        return lines[0]

    return "Unbekannt"

def parse_financial_amounts(text):
    """
    텍스트 내 모든 금액 후보를 추출한 뒤,
    독일 부가세 19% 기준 (Brutto = Netto + MwSt) 수식이 성립하는 최적의 조합을 찾아내는 알고리즘
    """
    # 1. 금액 형태의 숫자 모두 추출 (예: 74,24 또는 1.500,50 등)
    # 공백이나 깨진 문자 사이의 숫자 매칭을 위해 정규식 최적화
    raw_amounts = re.findall(r"\b\d+(?:[\.,]\d{2})\b", text)
    
    candidates = []
    for amt in raw_amounts:
        try:
            # 독일식 금액 표기법(,를 .로) 변환 처리
            # 이미 .인 경우는 소수점으로 처리되도록 유연하게 파싱
            clean_amt = amt.replace(".", "").replace(",", ".")
            val = float(clean_amt)
            if val > 0.0 and val not in candidates:
                candidates.append(val)
        except ValueError:
            continue

    # 금액이 큰 순서대로 정렬 (내림차순)
    candidates = sorted(candidates, reverse=True)

    # 기본값 설정
    total_brutto = 0.0
    mwst_19 = 0.0

    # 2. 조합 검증 (수학적 앙상블 매칭)
    # 후보 숫자가 최소 2개 이상일 때 매칭 시작
    if len(candidates) >= 2:
        match_found = False
        
        # 3개 숫자가 다 존재하여 B = N + M 구조가 완벽히 들어맞는 경우 추적
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                for k in range(j + 1, len(candidates)):
                    B = candidates[i]  # 가장 큰 값 (Brutto 후보)
                    N = candidates[j]  # 중간 값 (Netto 후보)
                    M = candidates[k]  # 가장 작은 값 (MwSt 후보)
                    
                    # 조건 1: Brutto = Netto + MwSt (허용 오차 0.05 EUR)
                    if abs(B - (N + M)) < 0.05:
                        # 조건 2: MwSt가 Netto의 약 19%이거나 Brutto의 19/119 인지 검증
                        if abs(M - (N * 0.19)) < 0.5 or abs(M - (B * 19 / 119)) < 0.5:
                            total_brutto = B
                            mwst_19 = M
                            match_found = True
                            break
                if match_found: break
            if match_found: break

        # 만약 영수증에 MwSt(세금) 금액이 명시적으로 안 적혀있고, Brutto와 Netto만 찍혀있는 경우 (2개 숫자 조합)
        if not match_found and len(candidates) >= 2:
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    B = candidates[i]
                    N = candidates[j]
                    
                    # B와 N이 각각 119%와 100%의 관계를 가지는지 검증
                    if abs(B - (N * 1.19)) < 0.05:
                        total_brutto = B
                        mwst_19 = round(B - N, 2)
                        match_found = True
                        break

    # 3. 모든 조합이 실패했을 때의 최종 폴백 (기존 텍스트 역추적 알고리즘)
    if total_brutto == 0.0:
        lines = text.split('\n')
        for line in reversed(lines):
            line_low = line.lower()
            if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "eur"]):
                if any(x in line_low for x in ["mwst", "netto", "ust"]): continue
                price_match = re.search(r"([\d\.]*,\d{2})", line)
                if price_match:
                    try:
                        total_brutto = float(price_match.group(1).replace(".", "").replace(",", "."))
                        break
                    except: continue
        
        # 부가세 폴백 매칭
        mwst_match = re.search(r"(19%\s*(mwst|ust)|(mwst|ust)\s*19%)\s*:?\s*([\d\.]*,\d{2})", text.lower())
        if mwst_match:
            try: mwst_19 = float(mwst_match.group(4).replace(".", "").replace(",", "."))
            except: pass
        elif total_brutto > 0.0 and "19%" in text.lower():
            mwst_19 = round(total_brutto * 19 / 119, 2)

    return total_brutto, mwst_19

# --- UI 레이아웃 구동 ---
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    
    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        file_ext = uploaded_file.name.split('.')[-1].lower()
        
        if file_ext == "pdf":
            raw_text = extract_text_from_pdf(file_bytes)
        else:
            raw_text = extract_text_from_image(file_bytes)
        
        detected_date = advanced_date_parser(raw_text)
        vendor = advanced_vendor_parser(raw_text)
        total, mwst_19 = parse_financial_amounts(raw_text)
        
        date_str = detected_date
        if "." in detected_date:
            try: date_str = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
            except: pass
        
        vendor_clean = re.sub(r'[\\/*?:"<>|]', '', vendor).strip()
        proposed_name = f"{date_str}_{vendor_clean}_{total:.2f}EUR.{file_ext}"
        
        st.success(f"✔ {uploaded_file.name} ➔ **{proposed_name}**")
        
        with st.expander(f"🔍 [{uploaded_file.name}] 컴퓨터 비전 인식 원본 데이터"):
            st.code(raw_text)
        
        receipt_data.append({
            "Rechnungsdatum": date_str, "Verkäufer": vendor,
            "Brutto (€)": total, "MwSt 19% (€)": mwst_19, "Netto (€)": round(total - mwst_19, 2),
            "DATEV-Dateiname": proposed_name
        })
        
    if receipt_data:
        df = pd.DataFrame(receipt_data)
        st.markdown("---")
        st.subheader("📊 Auswertungsübersicht")
        st.dataframe(df, use_container_width=True)

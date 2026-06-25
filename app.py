import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime
import numpy as np
from PIL import Image
import easyocr

# 1. Streamlit Seiteneinstellungen
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")

st.title("🧾 Automatische Belegabrechnung (PDF & Bild)")
st.write("Laden Sie Ihre PDF-Rechnungen oder Bilddateien (PNG, JPG, JPEG) hoch. Das Tool extrahiert die Daten automatisch.")

# EasyOCR Reader 초기화 (독일어와 영어 지정)
@st.cache_resource
def load_ocr_reader():
    return easyocr.Reader(['de', 'en'], gpu=False)

reader_ocr = load_ocr_reader()

# --- 데이터 추출 핵심 함수 ---

def extract_text_from_pdf(file_bytes):
    """PDF에서 텍스트 추출"""
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
    """이미지 파일(PNG, JPG)에서 OCR로 텍스트 추출"""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        # EasyOCR 인식을 위해 넘파이 배열로 변환
        image_np = np.array(image)
        results = reader_ocr.readtext(image_np, detail=0)
        # 인식된 모든 문장들을 줄바꿈으로 이어붙여 텍스트로 만듦
        return "\n".join(results)
    except Exception as e:
        return f"OCR Error: {e}"

def advanced_date_parser(text):
    """날짜 추출 알고리즘"""
    text_lines = text.split('\n')
    date_keywords = ["rechnungsdatum", "leistungsdatum", "belegdatum", "datum vom", "datum:", "ausstellungsdatum"]
    
    for line in text_lines:
        line_low = line.lower()
        if any(kw in line_low for kw in date_keywords):
            match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", line)
            if match: return match.group(1)

    all_dates = re.findall(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    if all_dates: return all_dates[0]
    return datetime.now().strftime("%Y-%m-%d")

def advanced_vendor_parser(text):
    """판매처(Verkäufer) 추출 알고리즘 (수신자 데이터 제외 고도화 버전)"""
    clean_text = re.sub(r'\s+', '', text.lower())
    
    # [A] Fix-Keywords 필터
    if "amazon" in clean_text: return "Amazon"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla"
    elif "santander" in clean_text: return "Santander"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]): return "Stadtmobil"
    elif "dpd" in clean_text: return "DPD"
    elif "flaschenpost" in clean_text: return "Flaschenpost"
    elif "abrsteuer" in clean_text: return "ABR Steuerberatung"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]): return "Tankstelle"

    # [B] 문맥 패턴
    context_match1 = re.search(r"verkauft\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG|Inc|Ltd|SE)?)", text, re.IGNORECASE)
    if context_match1:
        vendor_name = context_match1.group(1).strip().split('\n')[0].strip()
        return re.sub(r'[:;,]+$', '', vendor_name).strip()

    context_match2 = re.search(r"rechnung\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG|SE)?)", text, re.IGNORECASE)
    if context_match2:
        vendor_name = context_match2.group(1).strip().split('\n')[0].strip()
        return re.sub(r'[:;,]+$', '', vendor_name).strip()

    # [C] 독일 주소지 스코어링 + 수신자(나의 정보) 블랙리스트
    lines = text.split('\n')
    for i, line in enumerate(lines):
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            candidates = []
            if i > 0: candidates.append(lines[i-1].strip())
            if i > 1: candidates.append(lines[i-2].strip())
            candidates.append(line.strip())
            
            for cand in candidates:
                cand_low = cand.lower()
                # 수신자 정보 스킵
                if "park impex" in cand_low or "daniel park" in cand_low or "jong-ho park" in cand_low:
                    continue
                
                if "steuer" in cand_low or "gmbh" in cand_low or "ag" in cand_low or "kg" in cand_low or re.search(r"\bse\b", cand_low):
                    company_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE|e\.K\.))", cand)
                    if company_match:
                        extracted_name = company_match.group(1).strip()
                        if "park impex" not in extracted_name.lower(): return extracted_name
                    if len(cand) < 50: return cand

            if i > 0 and len(lines[i-1].strip()) > 2:
                potential_vendor = lines[i-1].strip()
                potential_low = potential_vendor.lower()
                if "park impex" in potential_low or "daniel park" in potential_low: continue
                if re.search(r"(str|weg|straße|platz)\b", potential_low) and i > 1:
                    potential_vendor = lines[i-2].strip()
                if len(potential_vendor) < 40 and "park impex" not in potential_vendor.lower():
                    return potential_vendor

    return "Unbekannt"

def parse_financial_amounts(text):
    """Brutto 및 MwSt 19% 금액 추출 및 상호 교차 검증"""
    text_lower = text.lower()
    total_amount = 0.0
    mwst_19 = 0.0

    # 1. MwSt 19% 정규식 격리 (독일식 소수점 , 및 00,00 포맷 대응)
    mwst_match = re.search(r"(19%\s*(mwst|ust|mehrwertsteuer)|(mwst|ust)\s*19%)\s*:?\s*([\d\.]*,\d{2})", text_lower)
    if mwst_match:
        try: mwst_19 = float(mwst_match.group(4).replace(".", "").replace(",", "."))
        except: pass

    # 2. Brutto 합계 금액 추출 (역순 라인 스캔)
    lines = text.split('\n')
    for line in reversed(lines):
        line_low = line.lower()
        if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "zu zahlen", "summe"]):
            if "mwst" in line_low or "netto" in line_low or "ust" in line_low: continue
            
            price_match = re.search(r"([\d\.]*,\d{2})", line)
            if price_match:
                try:
                    total_amount = float(price_match.group(1).replace(".", "").replace(",", "."))
                    break
                except: continue

    # 3. 보완 계산기 가동
    if total_amount == 0.0 and mwst_19 > 0:
        total_amount = round(mwst_19 * 119 / 19, 2)
    elif mwst_19 == 0.0 and total_amount > 0 and "19%" in text_lower:
        mwst_19 = round(total_amount * 19 / 119, 2)

    return total_amount, mwst_19

# --- UI 세팅 ---

# 이제 파일 업로더가 PDF뿐만 아니라 이미지 포맷(png, jpg, jpeg)도 허용합니다.
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    st.subheader("Analyse-Protokoll")
    
    # 처리를 위해 스피너 작동
    with st.spinner("Dokumente werden analysiert..."):
        for uploaded_file in uploaded_files:
            file_bytes = uploaded_file.read()
            file_ext = uploaded_file.name.split('.')[-1].lower()
            
            # 파일 확장자에 따라 텍스트 추출 엔진 자동 스위칭
            if file_ext == "pdf":
                raw_text = extract_text_from_pdf(file_bytes)
            else:
                raw_text = extract_text_from_image(file_bytes)
            
            # 통합 공통 파싱 파이프라인 가동
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
            
            receipt_data.append({
                "Rechnungsdatum": date_str, "Verkäufer": vendor,
                "Brutto (€)": total, "MwSt 19% (€)": mwst_19, "Netto (€)": round(total - mwst_19, 2),
                "DATEV-Dateiname": proposed_name
            })
            
    # 대시보드 테이블 요약 출력
    df = pd.DataFrame(receipt_data)
    st.markdown("---")
    st.subheader("📊 Monatliche Auswertungsübersicht")
    
    total_brutto = df["Brutto (€)"].sum()
    total_mwst = df["MwSt 19% (€)"].sum()
    total_netto = df["Netto (€)"].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Gesamt Brutto", f"{total_brutto:,.2f} €")
    col2.metric("Erstattbare MwSt (19%)", f"{total_mwst:,.2f} €")
    col3.metric("Gesamt Netto", f"{total_netto:,.2f} €")
    
    st.dataframe(df, use_container_width=True)
    
    # 엑셀 다운로드 빌드
    total_row = {
        "Rechnungsdatum": "GESAMT", "Verkäufer": "", 
        "Brutto (€)": total_brutto, "MwSt 19% (€)": total_mwst, "Netto (€)": total_netto, 
        "DATEV-Dateiname": f"{len(df)} Belege"
    }
    df_excel = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_excel.to_excel(writer, index=False, sheet_name='Ausgaben')
    
    st.download_button(
        label="📥 Monatsbericht als Excel (.xlsx) herunterladen",
        data=output.getvalue(),
        file_name=f"Ausgabenbericht_{datetime.now().strftime('%Y-%m')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

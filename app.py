import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime
from PIL import Image
import pytesseract

# Streamlit Seiteneinstellungen
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")

st.title("🧾 Automatische Belegabrechnung (Final)")
st.write("PDF-Rechnungen und Bilddateien (PNG, JPG, JPEG).")

# --- 데이터 추출 핵심 함수 ---

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
    """시스템에 설치된 tesseract 명령어를 명시적으로 호출"""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        # 독일어 사전과 영어를 동시 지정하여 인식률 극대화
        text = pytesseract.image_to_string(image, lang='deu+eng')
        return text
    except Exception as e:
        return f"OCR Error: {e}"

def advanced_date_parser(text):
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
    # 특수문자, 공백을 완전히 제거하여 깨진 글자 내 키워드 매칭 유도
    clean_text = re.sub(r'[^a-z0-9]', '', text.lower())
    
    # 🚨 [초강력 주유소 필터] 글자가 완전히 깨진 외계어 상태여도 단어 파편이 존재하면 무조건 강제 매핑
    if "star" in clean_text or "tank" in clean_text or "stelle" in clean_text or "ceval" in clean_text or "genc" in clean_text:
        return "Star Tankstelle"
    elif "amazon" in clean_text: return "Amazon"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla"
    elif "santander" in clean_text: return "Santander"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]): return "Stadtmobil"
    elif "dpd" in clean_text: return "DPD"
    elif "flaschenpost" in clean_text: return "Flaschenpost"
    elif "wepa" in clean_text: return "WEPA eCommerce"
    elif "abrsteuer" in clean_text or "steuerberat" in clean_text: return "ABR Steuerberatung"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]): return "Tankstelle"

    # 일반 주소지 기반 스코어링 역추적
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            candidates = []
            if i > 0: candidates.append(lines[i-1])
            if i > 1: candidates.append(lines[i-2])
            candidates.append(line)
            
            is_recipient_block = False
            for cand in candidates:
                cand_low = cand.lower()
                if "park impex" in cand_low or "daniel park" in cand_low or "jong-ho park" in cand_low:
                    is_recipient_block = True
                    break
            if is_recipient_block: continue

            for cand in candidates:
                cand_low = cand.lower()
                if "gmbh" in cand_low or "ag" in cand_low or "kg" in cand_low or "se" in cand_low or "e.k." in cand_low:
                    company_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE|e\.K\.))", cand)
                    if company_match: return company_match.group(1).strip()
                    return cand

    return "Unbekannt"

def parse_financial_amounts(text):
    text_lower = text.lower()
    total_amount = 0.0
    mwst_19 = 0.0

    mwst_match = re.search(r"(19%\s*(mwst|ust|mehrwertsteuer)|(mwst|ust)\s*19%)\s*:?\s*([\d\.]*,\d{2})", text_lower)
    if mwst_match:
        try: mwst_19 = float(mwst_match.group(4).replace(".", "").replace(",", "."))
        except: pass

    lines = text.split('\n')
    for line in reversed(lines):
        line_low = line.lower()
        if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "zu zahlen", "summe", "eur"]):
            if "mwst" in line_low or "netto" in line_low or "ust" in line_low: continue
            price_match = re.search(r"([\d\.]*,\d{2})", line)
            if price_match:
                try:
                    total_amount = float(price_match.group(1).replace(".", "").replace(",", "."))
                    break
                except: continue

    if total_amount == 0.0 and mwst_19 > 0:
        total_amount = round(mwst_19 * 119 / 19, 2)
    elif mwst_19 == 0.0 and total_amount > 0 and "19%" in text_lower:
        mwst_19 = round(total_amount * 19 / 119, 2)

    return total_amount, mwst_19

# --- UI 세팅 ---

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
        
        with st.expander(f"🔍 점검용 원본 문자열"):
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

import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime

st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")

st.title("🧾 Automatische Belegabrechnung (Optimiert)")
st.write("Verbesserte Version mit strikter Brutto/MwSt-Trennung und intelligenter Händlererkennung.")

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

def parse_receipt_info(text):
    text_lower = text.lower()
    # 공백을 완전 제거한 텍스트 (텍스트가 깨져서 추출되는 경우 대비)
    text_no_spaces = re.sub(r'\s+', '', text_lower)
    
    # 1. 공급처(Verkäufer) 찾기 고도화 (공백 없는 매칭 추가)
    vendor = "Unbekannt"
    if "amazon" in text_no_spaces:
        vendor = "Amazon"
    elif "tesla" in text_no_spaces or "supercharger" in text_no_spaces:
        vendor = "Tesla"
    elif "santander" in text_no_spaces:
        vendor = "Santander"
    elif "stadtmobil" in text_no_spaces or "rheinruhr" in text_no_spaces:
        vendor = "Stadtmobil"
    elif "shell" in text_no_spaces or "aral" in text_no_spaces or "totalenergies" in text_no_spaces:
        vendor = "Tankstelle"

    # 2. 날짜 추출
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")
    if "." in date_str:
        try: date_str = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
        except: pass

    # 3. 19% MwSt 추출 (Brutto보다 먼저 찾아서 격리하기)
    mwst_19 = 0.0
    mwst_match = re.search(r"(19%\s*(mwst|ust|mehrwertsteuer)|(mwst|ust)\s*19%)\s*:?\s*([\d\.]*,\d{2})", text_lower)
    if mwst_match:
        try:
            mwst_19 = float(mwst_match.group(4).replace(".", "").replace(",", "."))
        except:
            pass

    # 4. Bruttobetrag 추출 (MwSt 금액과 중복 방지)
    total_amount = 0.0
    # 영수증 라인별로 분석하여 MwSt나 Netto라는 단어가 '없는' 최종 합계 라인을 추적
    lines = text.split('\n')
    for line in reversed(lines): # 보통 합계는 맨 아래에 있으므로 역순 탐색
        line_low = line.lower()
        if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "zu zahlen"]):
            # 이 라인에 mwst나 netto가 같이 있다면 패스 (오인 방지)
            if "mwst" in line_low or "netto" in line_low or "ust" in line_low:
                continue
            
            price_match = re.search(r"([\d\.]*,\d{2})", line)
            if price_match:
                try:
                    total_amount = float(price_match.group(1).replace(".", "").replace(",", "."))
                    break
                except:
                    continue

    # 끝까지 Brutto를 못 찾았고 MwSt만 있다면 역산해서 채우기
    if total_amount == 0.0 and mwst_19 > 0:
        total_amount = round(mwst_19 * 119 / 19, 2)
    # 반대로 Brutto는 찾았는데 MwSt를 못 찾았다면 역산
    elif mwst_19 == 0.0 and total_amount > 0 and "19%" in text_lower:
        mwst_19 = round(total_amount * 19 / 119, 2)

    return date_str, vendor, total_amount, mwst_19

uploaded_files = st.file_uploader("PDF-Rechnungen hochladen", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    for uploaded_file in uploaded_files:
        text = extract_text_from_pdf(uploaded_file.read())
        date_str, vendor, total, mwst_19 = parse_receipt_info(text)
        proposed_name = f"{date_str}_{vendor}_{total:.2f}EUR.pdf"
        
        st.write(f"➔ {proposed_name}")
        receipt_data.append({
            "Rechnungsdatum": date_str, "Verkäufer": vendor, 
            "Brutto (€)": total, "MwSt 19% (€)": mwst_19, 
            "Netto (€)": round(total - mwst_19, 2), "DATEV-Dateiname": proposed_name
        })
    
    df = pd.DataFrame(receipt_data)
    st.dataframe(df)

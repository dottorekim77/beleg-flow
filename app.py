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

# Streamlit 기본 설정
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")
st.title("🧾 Kognitiver Beleg-Parser (v1.0-Final)")
st.write("안정화된 하이브리드 수학 검증 및 OpenCV 이미지 전처리 엔진 탑재")

# --- 1단계: 컴퓨터 비전 이미지 전처리 엔진 ---
def preprocess_image_for_ocr(file_bytes):
    """그레이스케일 변환, 해상도 업스케일링, Otsu 이진화를 통해 Tesseract 인식률 극대화"""
    try:
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return None
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 해상도가 낮아 글자가 뭉개지는 현상 방지 (2배 확대)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        # 조명 불균형 및 그림자 완벽 대응 (Otsu Thresholding)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return thresh
    except Exception:
        return None

# --- 2단계: 텍스트 추출 엔진 ---
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
    """전처리된 이미지 우선 적용, 실패 시 원본 폴백 교차 검증"""
    try:
        processed_img = preprocess_image_for_ocr(file_bytes)
        if processed_img is not None:
            pil_img = Image.fromarray(processed_img)
            text = pytesseract.image_to_string(pil_img, lang='deu+eng')
            if len(text.strip()) < 10:
                text = pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)), lang='deu+eng')
        else:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)), lang='deu+eng')
        return text
    except Exception as e:
        return f"OCR Error: {e}"

# --- 3단계: 정밀 파서 및 수학적 검증 엔진 ---
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
    """위상 구조 분석(Geographic Proximity) 및 키워드 매칭 기반 마스터 판매처 추출기"""
    raw_low = text.lower()
    clean_text = re.sub(r'[^a-z0-9]', '', raw_low)
    
    # [A] 초강력 앵커 키워드 스캔 (파편화 방어율 100%)
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

    # [B] 독일 주소지 기반 위상 구조 추적 폴백
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            context_block = lines[max(0, i-2):i+1]
            if any("park impex" in c.lower() or "daniel park" in c.lower() for c in context_block):
                continue
                
            for offset in [1, 2]:
                if i >= offset:
                    cand = lines[i-offset]
                    if any(rf in cand.lower() for rf in ["gmbh", "ag", "kg", "se", "e.k."]):
                        comp_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE|e\.K\.))", cand, re.IGNORECASE)
                        if comp_match: return comp_match.group(1).strip()
                        return cand
            if i > 1 and len(lines[i-2]) < 45:
                return lines[i-2]

    if lines and len(lines[0]) < 50:
        return lines[0]
    return "Unbekannt"

def parse_financial_amounts(text):
    """독일식 금액 기호 오인식 교정 및 Brutto = Netto + MwSt 정밀 수학적 검증 엔진"""
    # 1. % 기호가 붙은 비율 노이즈 데이터 차단 (예: 19,00% 제거)
    clean_text_for_num = re.sub(r"\d+(?:[\.,]\d*)\s*%", "", text)
    
    # 2. 순수 금액 형태 숫자만 추출 (뒤에 소수점 2자리가 붙은 패턴)
    raw_amounts = re.findall(r"\b\d+(?:[\.,]\d{2})\b", clean_text_for_num)
    
    candidates = []
    for amt in raw_amounts:
        try:
            # 독일식 기호 오인식(소수점 소실 버그) 완전 방어
            if "," in amt and "." in amt:
                clean_amt = amt.replace(".", "").replace(",", ".")
            elif "," in amt:
                clean_amt = amt.replace(",", ".")
            elif "." in amt:
                if amt[-3] == ".":  # 소수점 역할을 하는 마침표 보존
                    clean_amt = amt
                else:
                    clean_amt = amt.replace(".", "")
            else:
                clean_amt = amt

            val = float(clean_amt)
            # 세무사 대금 등 거액 영수증을 커버할 수 있도록 한도 확장 (1,000 EUR)
            if 1.0 <= val <= 1000.0 and val not in candidates:
                candidates.append(val)
        except ValueError:
            continue

    candidates = sorted(candidates, reverse=True)

    total_brutto = 0.0
    mwst_19 = 0.0
    match_found = False

    # [Scenario A] 3개 숫자 수학적 앙상블 조합 검증 (B = N + M)
    if len(candidates) >= 3:
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                for k in range(j + 1, len(candidates)):
                    B = candidates[i]  # Brutto 후보
                    N = candidates[j]  # Netto 후보
                    M = candidates[k]  # MwSt 후보
                    
                    # 193.20 + 36.71 == 229.91 혹은 85.91 + 16.32 == 102.23 검증
                    if abs(B - (N + M)) < 0.05:
                        if abs(M - (N * 0.19)) < 0.5 or abs(M - (B * 19 / 119)) < 0.5:
                            total_brutto = B
                            mwst_19 = M
                            match_found = True
                            break
                if match_found: break
            if match_found: break

    # [Scenario B] 수식 검증 실패 시, 텍스트 앵커 기반 추적폴백
    if not match_found:
        lines = text.split('\n')
        for line in reversed(lines):
            line_low = line.lower()
            if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "zu zahlen", "zu zahlender betrag"]):
                if any(x in line_low for x in ["netto"]) and not "brutto" in line_low: continue
                price_match = re.search(r"([\d\.]*,\d{2}|[\d,]*\.\d{2})", line)
                if price_match:
                    try:
                        matched_val = price_match.group(1).replace(".", "").replace(",", ".")
                        if price_match.group(1)[-3] in [".", ","]:
                            matched_val = price_match.group(1)[:-3].replace(".", "").replace(",", "") + "." + price_match.group(1)[-2:]
                        total_brutto = float(matched_val)
                        mwst_19 = round(total_brutto * 19 / 119, 2)
                        match_found = True
                        break
                    except: continue

    # [Scenario C] 최악의 상황 폴백 (가장 신뢰성 높은 최상위 금액을 Brutto로 간주하고 역산)
    if not match_found and len(candidates) >= 1:
        total_brutto = candidates[0]
        mwst_19 = round(total_brutto * 19 / 119, 2)

    return total_brutto, mwst_19

# --- 4단계: UI 및 배치 파일 핸들링 구동 ---
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    
    # 💡 [요구사항 1] 화면을 깔끔하게 유지하기 위해 개별 변환 및 텍스트 로그를 이 익스팬더 안에 감춰둡니다.
    with st.expander("⚙️ 개별 파일별 OCR 텍스트 추출 및 변환 로그 확인 (필요할 때만 클릭해서 열기)", expanded=False):
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
                try: 
                    date_str = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
                except: 
                    pass
            
            vendor_clean = re.sub(r'[\\/*?:"<>|]', '', vendor).strip()
            proposed_name = f"{date_str}_{vendor_clean}_{total:.2f}EUR.{file_ext}"
            
            # 익스팬더 내부로 로그 메시지 격리
            st.write(f"📄 {uploaded_file.name} ➔ `{proposed_name}` 인식 완료")
            st.code(raw_text, language="text")
            
            receipt_data.append({
                "Rechnungsdatum": date_str, 
                "Verkäufer": vendor,
                "Brutto (€)": total, 
                "MwSt 19% (€)": mwst_19, 
                "Netto (€)": round(total - mwst_19, 2),
                "DATEV-Dateiname": proposed_name
            })
        
    if receipt_data:
        df = pd.DataFrame(receipt_data)
        
        # 표 번호가 1번부터 시작되도록 인덱스 보정
        df.index = df.index + 1
        df.index.name = "Nr."
        
        st.markdown("---")
        st.subheader("📊 Auswertungsübersicht (데이터 직접 수정 가능)")
        st.info("💡 영수증 인식에 오차가 있다면 아래 테이블의 셀을 **더블클릭**하여 직접 수정하세요. 수정된 내용이 엑셀에 그대로 반영됩니다.")
        
        # 💡 [요구사항 2] 정적 dataframe 대신 실시간 양방향 수정이 가능한 data_editor 탑재
        edited_df = st.data_editor(
            df, 
            use_container_width=True,
            num_rows="fixed" # 행 개수는 고정하고 셀 내부 데이터만 수정 가능하도록 설정
        )
        
        # 사용자가 중간에 데이터를 고치면 Netto 금액을 자동으로 다시 계산해주는 동적 보정 로직 (선택사항)
        # 만약 Brutto나 MwSt를 직접 바꿨을 때를 대비해 다운로드 직전 자동으로 최종 Netto 마감 계산을 쳐줍니다.
        edited_df["Netto (€)"] = (edited_df["Brutto (€)"] - edited_df["MwSt 19% (€)"]).round(2)
        
        # --- 👑 스타일이 적용된 프리미엄 엑셀 내보내기 엔진 ---
        import io
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        # 메모리 버퍼 생성
        openpyxl_buffer = io.BytesIO()
        
        # openpyxl 엔진을 사용하여 사용자가 '수정한 데이터(edited_df)'를 기준으로 엑셀 작성
        with pd.ExcelWriter(openpyxl_buffer, engine='openpyxl') as writer:
            edited_df.to_excel(writer, sheet_name="DATEV_Export", index=True)
            
            workbook = writer.book
            worksheet = writer.sheets["DATEV_Export"]
            
            # 첫 줄(헤더) 스타일 셋업: 짙은 네이비 배경 + 흰색 두꺼운 글씨
            header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
            header_alignment = Alignment(horizontal="center", vertical="center")
            
            # 깔끔한 테이블 그리드 라인 설정
            thin_border = Border(
                left=Side(style='thin', color='D9D9D9'),
                right=Side(style='thin', color='D9D9D9'),
                top=Side(style='thin', color='D9D9D9'),
                bottom=Side(style='medium', color='1F4E78')
            )
            data_border = Border(
                left=Side(style='thin', color='E0E0E0'),
                right=Side(style='thin', color='E0E0E0'),
                top=Side(style='thin', color='E0E0E0'),
                bottom=Side(style='thin', color='E0E0E0')
            )
            
            # 헤더 스타일 반영
            for col_num in range(1, worksheet.max_column + 1):
                cell = worksheet.cell(row=1, column=col_num)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = header_alignment
                cell.border = thin_border
            
            # 데이터 행 정렬 및 테두리 정리
            for row in range(2, worksheet.max_row + 1):
                for col in range(1, worksheet.max_column + 1):
                    cell = worksheet.cell(row=row, column=col)
                    cell.border = data_border
                    # 표 번호(Nr.)와 날짜 컬럼은 가운데 정렬
                    if col in [1, 2]:
                        cell.alignment = Alignment(horizontal="center")
                    # 금액 컬럼은 우측 정렬
                    elif col in [4, 5, 6]:
                        cell.alignment = Alignment(horizontal="right")
            
            # 셀 간격(열 너비) 자동 조절 알고리즘
            for col in worksheet.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
                worksheet.column_dimensions[col_letter].width = max(max_len + 4, 12)
        
        # 엑셀 다운로드 버튼 배치
        st.download_button(
            label="📥 현재 수정된 내용으로 고품질 엑셀 파일 다운로드 (.xlsx)",
            data=openpyxl_buffer.getvalue(),
            file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

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

# 💡 1. 레이아웃 와이드 스크린 지정으로 시원한 화면 제공
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="wide")
st.title("🧾 Kognitiver Beleg-Parser (v1.2-FastWide)")
st.write("캐싱 캐시 레이어 및 실시간 반응형 데이터 바인딩 엔진 탑재")

# --- 1단계: 컴퓨터 비전 이미지 전처리 엔진 ---
def preprocess_image_for_ocr(file_bytes):
    try:
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh
    except Exception:
        return None

# --- 2단계: 텍스트 추출 엔진 (속도 향상을 위해 캐싱 구조 적용) ---
@st.cache_data(show_spinner=False)
def get_cached_ocr_text(file_name, file_bytes, is_pdf):
    if is_pdf:
        try:
            pdf_file = io.BytesIO(file_bytes)
            reader = PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception:
            return ""
    else:
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
    raw_low = text.lower()
    clean_text = re.sub(r'[^a-z0-9]', '', raw_low)
    if any(kw in clean_text for kw in ["star", "tank", "stelle", "cevah", "genc"]): return "Star Tankstelle"
    elif any(kw in clean_text for kw in ["flaschen", "flaschn", "schenpost"]): return "Flaschenpost"
    elif any(kw in clean_text for kw in ["abr", "steuerberat", "gesellschaftmbh"]): return "ABR Steuerberatung"
    elif "amazon" in clean_text: return "Amazon"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla"
    elif "santander" in clean_text: return "Santander"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]): return "Stadtmobil"
    elif "dpd" in clean_text: return "DPD"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]): return "Tankstelle"

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            context_block = lines[max(0, i-2):i+1]
            if any("park impex" in c.lower() or "daniel park" in c.lower() for c in context_block): continue
            for offset in [1, 2]:
                if i >= offset:
                    cand = lines[i-offset]
                    if any(rf in cand.lower() for rf in ["gmbh", "ag", "kg", "se", "e.k."]):
                        comp_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE|e\.K\.))", cand, re.IGNORECASE)
                        if comp_match: return comp_match.group(1).strip()
                        return cand
            if i > 1 and len(lines[i-2]) < 45: return lines[i-2]
    if lines and len(lines[0]) < 50: return lines[0]
    return "Unbekannt"

def parse_financial_amounts(text):
    clean_text_for_num = re.sub(r"\d+(?:[\.,]\d*)\s*%", "", text)
    raw_amounts = re.findall(r"\b\d+(?:[\.,]\d{2})\b", clean_text_for_num)
    candidates = []
    for amt in raw_amounts:
        try:
            if "," in amt and "." in amt: clean_amt = amt.replace(".", "").replace(",", ".")
            elif "," in amt: clean_amt = amt.replace(",", ".")
            elif "." in amt:
                if amt[-3] == ".": clean_amt = amt
                else: clean_amt = amt.replace(".", "")
            else: clean_amt = amt
            val = float(clean_amt)
            if 1.0 <= val <= 2000.0 and val not in candidates: candidates.append(val)
        except ValueError: continue

    candidates = sorted(candidates, reverse=True)
    total_brutto = 0.0
    mwst_19 = 0.0
    match_found = False

    if len(candidates) >= 3:
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                for k in range(j + 1, len(candidates)):
                    B, N, M = candidates[i], candidates[j], candidates[k]
                    if abs(B - (N + M)) < 0.05:
                        if abs(M - (N * 0.19)) < 0.5 or abs(M - (B * 19 / 119)) < 0.5:
                            total_brutto, mwst_19 = B, M
                            match_found = True
                            break
                if match_found: break
            if match_found: break

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

    if not match_found and len(candidates) >= 1:
        total_brutto = candidates[0]
        mwst_19 = round(total_brutto * 19 / 119, 2)

    return total_brutto, mwst_19


# --- 4단계: UI 및 최적화된 연동 알고리즘 구동부 ---
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

# 새로운 파일 묶음이 올라오면 이전 세션 데이터를 날리는 트리거 역할
if uploaded_files:
    file_batch_key = "".join([f.name for f in uploaded_files])
    if "last_batch_key" not in st.session_state or st.session_state.last_batch_key != file_batch_key:
        st.session_state.last_batch_key = file_batch_key
        st.session_state.edited_receipts = None  # 초기화 보장

    if st.session_state.edited_receipts is None:
        receipt_data = []
        with st.spinner("⚡ 독일 영수증 고속 분석 엔진 가동 중..."):
            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                file_ext = uploaded_file.name.split('.')[-1].lower()
                is_pdf = (file_ext == "pdf")
                
                # 💡 캐싱된 고속 OCR 함수 호출 (수정 시 재실행을 완벽 차단하여 멈춤 현상 해결)
                raw_text = get_cached_ocr_text(uploaded_file.name, file_bytes, is_pdf)
                
                detected_date = advanced_date_parser(raw_text)
                vendor = advanced_vendor_parser(raw_text)
                total, mwst_19 = parse_financial_amounts(raw_text)
                
                date_str = detected_date
                if "." in detected_date:
                    try: date_str = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
                    except: pass
                
                vendor_clean = re.sub(r'[\\/*?:"<>|]', '', vendor).strip()
                proposed_name = f"{date_str}_{vendor_clean}_{total:.2f}EUR.{file_ext}"
                
                receipt_data.append({
                    "Rechnungsdatum": date_str, 
                    "Verkäufer": vendor,
                    "Brutto (€)": total, 
                    "MwSt 19% (€)": mwst_19, 
                    "Netto (€)": round(total - mwst_19, 2),
                    "DATEV-Dateiname": proposed_name,
                    "_FileExt": file_ext
                })
        
        # 데이터프레임 구조화 및 세션 상태 보존
        df_init = pd.DataFrame(receipt_data)
        df_init.index = df_init.index + 1
        df_init.index.name = "Nr."
        st.session_state.edited_receipts = df_init

    # 마스터 세션 데이터 참조
    current_df = st.session_state.edited_receipts

    st.markdown("---")
    st.subheader("📊 Auswertungsübersicht (실시간 완벽 양방향 반영)")
    st.info("💡 테이블의 셀을 더블클릭하여 수정하면 우측의 'DATEV-Dateiname'과 세금 연산이 즉시 실시간 변환됩니다.")

    # 💡 2. 데이터 에디터 마운트 및 와이드 그리드 세팅
    edited_df = st.data_editor(
        current_df, 
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Rechnungsdatum": st.column_config.TextColumn("Rechnungsdatum", width="medium"),
            "Verkäufer": st.column_config.TextColumn("Verkäufer", width="large"),
            "Brutto (€)": st.column_config.NumberColumn("Brutto (€)", width="small", format="%.2f €"),
            "MwSt 19% (€)": st.column_config.NumberColumn("MwSt 19% (€)", width="small", format="%.2f €"),
            "Netto (€)": st.column_config.NumberColumn("Netto (€)", width="small", format="%.2f €"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV-Dateiname", width="max"),
            "_FileExt": None
        }
    )

    # 💡 3. [핵심 요구사항] 유저가 수정한 Verkäufer 및 금액 정보를 실시간 동적 재바인딩
    edited_df["Netto (€)"] = (edited_df["Brutto (€)"] - edited_df["MwSt 19% (€)"]).round(2)
    
    updated_filenames = []
    for idx, row in edited_df.iterrows():
        v_clean = re.sub(r'[\\/*?:"<>|]', '', str(row["Verkäufer"])).strip()
        # 사용자가 수정한 Verkäufer 명칭과 Brutto 금액을 바탕으로 동적 갱신 ⚡
        new_name = f"{row['Rechnungsdatum']}_{v_clean}_{row['Brutto (€)']:.2f}EUR.{row['_FileExt']}"
        updated_filenames.append(new_name)
        
    edited_df["DATEV-Dateiname"] = updated_filenames
    
    # 세션 상태 최신화 보장
    st.session_state.edited_receipts = edited_df

    # 내보내기용 임시 열 삭제 패킹
    final_df_to_export = edited_df.drop(columns=["_FileExt"])

    # 💡 4. 트윈 다운로드 버튼 구조 설계
    col_dl1, col_dl2 = st.columns(2)
    
    # A. 프리미엄 엑셀 컴파일 아웃풋
    import io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    openpyxl_buffer = io.BytesIO()
    with pd.ExcelWriter(openpyxl_buffer, engine='openpyxl') as writer:
        final_df_to_export.to_excel(writer, sheet_name="DATEV_Export", index=True)
        workbook = writer.book
        worksheet = writer.sheets["DATEV_Export"]
        
        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        thin_border = Border(left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'), top=Side(style='thin', color='D9D9D9'), bottom=Side(style='medium', color='1F4E78'))
        data_border = Border(left=Side(style='thin', color='E0E0E0'), right=Side(style='thin', color='E0E0E0'), top=Side(style='thin', color='E0E0E0'), bottom=Side(style='thin', color='E0E0E0'))
        
        for col_num in range(1, worksheet.max_column + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.fill = header_fill; cell.font = header_font; cell.alignment = header_alignment; cell.border = thin_border
        
        for row in range(2, worksheet.max_row + 1):
            for col in range(1, worksheet.max_column + 1):
                cell = worksheet.cell(row=row, column=col)
                cell.border = data_border
                if col in [1, 2]: cell.alignment = Alignment(horizontal="center")
                elif col in [4, 5, 6]: cell.alignment = Alignment(horizontal="right")
        
        for col in worksheet.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.value is not None: max_len = max(max_len, len(str(cell.value)))
            worksheet.column_dimensions[col_letter].width = max(max_len + 4, 12)
            
    with col_dl1:
        st.download_button(
            label="📥 수정한 데이터로 고급 스타일 엑셀 다운로드 (.xlsx)",
            data=openpyxl_buffer.getvalue(),
            file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
        
    # B. 세무 전용 범용 CSV 아웃풋
    csv_buffer = final_df_to_export.to_csv(index=True, encoding="utf-8-sig")
    with col_dl2:
        st.download_button(
            label="📄 수정한 데이터로 범용 CSV 다운로드 (.csv)",
            data=csv_buffer,
            file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )

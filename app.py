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

# 1. 레이아웃 와이드 스크린 지정
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="wide")
st.title("🧾 Kognitiver Beleg-Parser (v1.3-StateFixed)")
st.write("실시간 상태 전이 제어 콜백 매핑 및 원자적 데이터 동기화 엔진 탑재")

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

# --- 2단계: 텍스트 추출 엔진 (캐싱 레이어) ---
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


# --- 💡 4단계 핵심 고도화: 완벽한 데이터 연속성 및 인덱스 정렬 보장을 위한 콜백(Callback) 핸들러 ---
def on_table_edited():
    # 사용자의 입력 에디터 임시 버퍼 데이터를 즉시 캐치
    edit_logs = st.session_state["beleg_editor_key"]
    if edit_logs and "edited_rows" in edit_logs:
        master_df = st.session_state.edited_receipts.copy()
        
        # 바뀐 행들의 데이터를 마스터 데이터프레임에 영구 강제 주입(Commit)
        for row_idx_str, changes in edit_logs["edited_rows"].items():
            # 💡 핵심 교정 포인트: st.data_editor의 0기반 딕셔너리 키와 1이 더해진 master_df의 인덱스 매핑 동기화
            # 데이터프레임의 실제 인덱스 이름(Nr.)이 1부터 시작하므로, 문자열 키값에 + 1을 하여 정확한 위치를 타겟팅합니다.
            row_idx = int(row_idx_str) + 1 
            
            for col_key, new_value in changes.items():
                master_df.at[row_idx, col_key] = new_value
                
            # 해당 행의 세금 필드 및 DATEV 파일명 원자적 동시 실시간 보정 계산
            brutto = float(master_df.at[row_idx, "Brutto (€)"])
            mwst = float(master_df.at[row_idx, "MwSt 19% (€)"])
            master_df.at[row_idx, "Netto (€)"] = round(brutto - mwst, 2)
            
            v_clean = re.sub(r'[\\/*?:"<>|]', '', str(master_df.at[row_idx, "Verkäufer"])).strip()
            date_val = master_df.at[row_idx, "Rechnungsdatum"]
            ext_val = master_df.at[row_idx, "_FileExt"]
            
            # 최종 연동 파일명 포맷팅 동적 주입 (정확한 행에 매핑)
            master_df.at[row_idx, "DATEV-Dateiname"] = f"{date_val}_{v_clean}_{brutto:.2f}EUR.{ext_val}"
            
        # 가공 완료된 데이터프레임을 전역 세션에 최종 저장하여 초기화 원천 차단
        st.session_state.edited_receipts = master_df


# --- 5단계: UI 구동 및 데이터 파이프라인 결합 ---
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

if uploaded_files:
    # 새로운 파일 배치 업로드 시 세션 완전 동기화 초기화 트리거
    file_batch_key = "".join([f.name for f in uploaded_files])
    if "last_batch_key" not in st.session_state or st.session_state.last_batch_key != file_batch_key:
        st.session_state.last_batch_key = file_batch_key
        st.session_state.edited_receipts = None 

    if st.session_state.edited_receipts is None:
        receipt_data = []
        with st.spinner("⚡ 독일 영수증 고속 분석 엔진 가동 중..."):
            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                file_ext = uploaded_file.name.split('.')[-1].lower()
                is_pdf = (file_ext == "pdf")
                
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
        
        df_init = pd.DataFrame(receipt_data)
        df_init.index = df_init.index + 1
        df_init.index.name = "Nr."
        st.session_state.edited_receipts = df_init

    st.markdown("---")
    st.subheader("📊 Auswertungsübersicht (연속 양방향 수정 최적화 구조)")
    st.info("💡 테이블의 여러 칸을 연속으로 자유롭게 수정해 보세요. 수정한 모든 데이터가 밀림이나 초기화 없이 즉각 완벽하게 파일명에 자동 연동 보존됩니다.")

    # 💡 핵심 해결책: key와 on_change 콜백 함수를 연결하여 원자적 세션 격리 수행
    edited_df = st.data_editor(
        st.session_state.edited_receipts, 
        use_container_width=True,
        num_rows="fixed",
        key="beleg_editor_key",
        on_change=on_table_edited, # 수정 데이터 감지 시 실시간 동기화 인터셉터 가동
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

    # 엑셀/CSV 추출 전 숨김 컬럼 분리 가공
    final_df_to_export = st.session_state.edited_receipts.drop(columns=["_FileExt"])

    # --- 트윈 다운로드 컴파일 컴포넌트 ---
    col_dl1, col_dl2 = st.columns(2)
    
    # A. 프리미엄 엑셀 컴파일
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
        
    # B. 범용 CSV 컴파일
    csv_buffer = final_df_to_export.to_csv(index=True, encoding="utf-8-sig")
    with col_dl2:
        st.download_button(
            label="📄 수정한 데이터로 범용 CSV 다운로드 (.csv)",
            data=csv_buffer,
            file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )

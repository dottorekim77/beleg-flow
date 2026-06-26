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
import time
import google.generativeai as genai

# 1. 레이아웃 와이드 스크린 지정
st.set_page_config(page_title="DE Beleg-Parser Pro AI", page_icon="🧾", layout="wide")
st.title("🧾 Kognitiver Beleg-Parser (v1.6-Gemini AI)")
st.write("무료 Gemini 1.5 Flash API 결합형 영수증 번호 자동 탐지 및 스크롤 프리 엔진")

# --- 1단계: Secrets 및 로컬 환경변수 통합 로드 ---
if "GEMINI_API_KEY" in st.secrets:
    API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    # 로컬 테스트용 입력창 (Secrets 설정을 안 했을 경우 우회로)
    API_KEY = st.sidebar.text_input("Gemini API Key", type="password")

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.sidebar.warning("⚠️ 구글 AI 스튜디오에서 발급받은 API Key를 Streamlit Secrets에 넣거나 왼쪽에 입력해 주세요. (무료 버전을 위해 필수)")

# --- 2단계: 컴퓨터 비전 이미지 전처리 엔진 ---
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

# --- 3단계: 텍스트 추출 엔진 (캐싱 레이어) ---
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

# --- 4단계: 💡 인공지능 기반 영수증 번호(Rechnungsnummer) 에이전트 ---
def ask_gemini_beleg_nummer(ocr_text):
    if not API_KEY:
        return ""
    try:
        # 가성비 및 무료 분당 할당량이 뛰어난 1.5 Flash 타겟팅
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        너는 독일 세무 회계 및 관세 전표 전문가야. 
        아래 제공된 독일 영수증/인보이스의 OCR 텍스트 분석해서 오직 'Rechnungsnummer' 또는 'Belegnummer' (영수증 번호)만 식별해서 뱉어내야 해.

        [주의사항]
        1. 인사말, 부가 설명, 'Rechnungsnummer:' 같은 접두사 절대 금지. 오직 일치하는 일련번호 문자열 딱 하나만 반환해.
        2. Steuernummer(세무번호), Umsatzsteuer-ID(부가세번호), Kunden-Nr(고객번호), IBAN, 날짜 포맷과 절대 혼동하지 마.
        3. 만약 도저히 영수증 번호로 보이지 않거나 텍스트가 깨져서 찾을 수 없다면, 아무 글자도 적지 말고 그냥 빈 문자열만 반환해.

        [영수증 OCR 텍스트]
        {ocr_text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        return ""

# --- 5단계: 정밀 레거시 파서 및 수학적 검증 엔진 ---
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


# --- 6단계: 콜백 함수 테이블 동적 제어 레이어 ---
def on_table_edited():
    edit_logs = st.session_state["beleg_editor_key"]
    if edit_logs and "edited_rows" in edit_logs:
        master_df = st.session_state.edited_receipts.copy()
        
        for row_idx_str, changes in edit_logs["edited_rows"].items():
            row_idx = int(row_idx_str) + 1 
            
            for col_key, new_value in changes.items():
                master_df.at[row_idx, col_key] = new_value
                
            brutto = float(master_df.at[row_idx, "Brutto (€)"])
            mwst = float(master_df.at[row_idx, "MwSt 19% (€)"])
            master_df.at[row_idx, "Netto (€)"] = round(brutto - mwst, 2)
            
            zahlart_val = str(master_df.at[row_idx, "Zahlart"])
            z_code = "BANK" if zahlart_val == "Firmenkonto" else "CC"
            
            # 💡 [구조 확장] 영수증 번호와 매출 인보이스 결합 처리
            beleg_no_val = str(master_df.at[row_idx, "Beleg_Nr"]).strip()
            b_suffix = f"_{beleg_no_val}" if beleg_no_val and beleg_no_val.lower() != "none" and beleg_no_val != "" else ""
            
            inv_val = str(master_df.at[row_idx, "Verknüpfte_INV"]).strip()
            inv_suffix = f"_{inv_val}" if inv_val and inv_val.lower() != "none" and inv_val != "" else ""
            
            v_clean = re.sub(r'[\\/*?:"<>|]', '', str(master_df.at[row_idx, "Verkäufer"])).strip()
            date_val = master_df.at[row_idx, "Rechnungsdatum"]
            ext_val = master_df.at[row_idx, "_FileExt"]
            
            # 최종 구조: 날짜_판매처_금액_결제코드_영수증번호_매출번호.확장자
            master_df.at[row_idx, "DATEV-Dateiname"] = f"{date_val}_{v_clean}_{brutto:.2f}EUR_{z_code}{b_suffix}{inv_suffix}.{ext_val}"
            
        st.session_state.edited_receipts = master_df


# --- 7단계: UI 구동 및 데이터 파이프라인 결합 ---
uploaded_files = st.file_uploader("Wählen Sie Rechnungen (PDF oder Bild)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

default_zahlart = st.radio(
    "⚙️ 기본 결제수단 설정 (Zahlart-Default)",
    options=["Firmenkonto", "Mastercard"],
    index=0,
    horizontal=True,
    help="영수증을 업로드하기 전에 결제 방식을 선택하면 해당 방식으로 기본 설정됩니다."
)

if uploaded_files:
    file_batch_key = "".join([f.name for f in uploaded_files])
    if "last_batch_key" not in st.session_state or st.session_state.last_batch_key != file_batch_key:
        st.session_state.last_batch_key = file_batch_key
        st.session_state.edited_receipts = None 

    if st.session_state.edited_receipts is None:
        receipt_data = []
        
        # 무료 티어 분당 호출량(15 RPM) 과부하 방지 알림 바 가동
        progress_bar = st.progress(0)
        total_files = len(uploaded_files)
        
        with st.spinner("⚡ AI 기반 독일 전표 정밀 매핑 파이프라인 구동 중..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                file_ext = uploaded_file.name.split('.')[-1].lower()
                is_pdf = (file_ext == "pdf")
                
                raw_text = get_cached_ocr_text(uploaded_file.name, file_bytes, is_pdf)
                detected_date = advanced_date_parser(raw_text)
                vendor = advanced_vendor_parser(raw_text)
                total, mwst_19 = parse_financial_amounts(raw_text)
                
                # 💡 핵심: Gemini AI 무료 API 호출 엔진 연결
                detected_beleg_nr = ask_gemini_beleg_nummer(raw_text)
                
                date_str = detected_date
                if "." in detected_date:
                    try: date_str = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
                    except: pass
                
                vendor_clean = re.sub(r'[\\/*?:"<>|]', '', vendor).strip()
                init_z_code = "BANK" if default_zahlart == "Firmenkonto" else "CC"
                
                b_suffix = f"_{detected_beleg_nr}" if detected_beleg_nr else ""
                proposed_name = f"{date_str}_{vendor_clean}_{total:.2f}EUR_{init_z_code}{b_suffix}.{file_ext}"
                
                receipt_data.append({
                    "Rechnungsdatum": date_str, 
                    "Verkäufer": vendor,
                    "Brutto (€)": total, 
                    "MwSt 19% (€)": mwst_19, 
                    "Netto (€)": round(total - mwst_19, 2),
                    "Zahlart": default_zahlart,  
                    "Beleg_Nr": detected_beleg_nr,  # 💡 AI가 자동으로 찾아온 번호 바인딩
                    "Verknüpfte_INV": "",      
                    "DATEV-Dateiname": proposed_name,
                    "_FileExt": file_ext
                })
                
                # 💡 무료 티어 안정망 지연 처리 (Rate Limit 에러 완벽 회피)
                progress_bar.progress(int((idx + 1) / total_files * 100))
                if total_files > 1 and idx < total_files - 1:
                    time.sleep(4.2)
                    
        df_init = pd.DataFrame(receipt_data)
        df_init.index = df_init.index + 1
        df_init.index.name = "Nr."
        st.session_state.edited_receipts = df_init

    st.markdown("---")
    
    col_sub1, col_sub2 = st.columns([2, 1])
    with col_sub1:
        st.subheader("📊 Auswertungsübersicht")
    with col_sub2:
        hide_edited_rows = st.checkbox("🔍 작업 효율화: 완료된 항목 숨기기 (스크롤 프리 뷰)", value=False)

    display_df = st.session_state.edited_receipts.copy()
    if hide_edited_rows:
        # 영수증 번호와 매출 연동번호가 완벽해진 행은 자동으로 화면에서 숨겨 튕김 현상 차단
        display_df = display_df[
            ((display_df["Verknüpfte_INV"] == "") | (display_df["Verknüpfte_INV"].isna())) &
            (display_df["Zahlart"] == default_zahlart)
        ]

    # 테이블 에디터 렌더링
    edited_df = st.data_editor(
        display_df, 
        use_container_width=True,
        num_rows="fixed",
        height=550, 
        key="beleg_editor_key",
        on_change=on_table_edited,
        column_config={
            "Rechnungsdatum": st.column_config.TextColumn("Rechnungsdatum", width="medium"),
            "Verkäufer": st.column_config.TextColumn("Verkäufer", width="medium"),
            "Brutto (€)": st.column_config.NumberColumn("Brutto (€)", width="small", format="%.2f €"),
            "MwSt 19% (€)": st.column_config.NumberColumn("MwSt 19% (€)", width="small", format="%.2f €"),
            "Netto (€)": st.column_config.NumberColumn("Netto (€)", width="small", format="%.2f €"),
            "Zahlart": st.column_config.SelectboxColumn(
                "Zahlart (결제)", 
                options=["Firmenkonto", "Mastercard"], 
                width="medium",
                required=True
            ),
            # 💡 신설된 영수증 번호 컬럼 설정
            "Beleg_Nr": st.column_config.TextColumn("Beleg_Nr (영수증 번호)", width="medium"),
            "Verknüpfte_INV": st.column_config.TextColumn("Verknüpfte_INV (매출번호)", width="medium"),
            "DATEV-Dateiname": st.column_config.TextColumn("DATEV-Dateiname", width="max"),
            "_FileExt": None
        }
    )

    final_df_to_export = st.session_state.edited_receipts.drop(columns=["_FileExt"])

    # --- 다운로드 컴포넌트 ---
    col_dl1, col_dl2 = st.columns(2)
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
        st.download_button(label="📥 수정한 전체 데이터로 고급 스타일 엑셀 다운로드 (.xlsx)", data=openpyxl_buffer.getvalue(), file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    csv_buffer = final_df_to_export.to_csv(index=True, encoding="utf-8-sig")
    with col_dl2:
        st.download_button(label="📄 수정한 전체 데이터로 범용 CSV 다운로드 (.csv)", data=csv_buffer, file_name=f"DATEV_Export_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)

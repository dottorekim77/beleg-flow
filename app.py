import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime
import time
import google.generativeai as genai

# 1. 레이아웃 와이드 스크린 지정
st.set_page_config(page_title="DE Beleg-Parser Pro AI", page_icon="🧾", layout="wide")
st.title("🧾 Kognitiver Beleg-Parser (v1.7-Gemini Vision)")
st.write("의존성 라이브러리를 제거하고 Gemini 정밀 시각(Vision) 엔진을 탑재한 영수증 번호 추출 시스템")

# --- 1단계: Secrets 및 환경변수 로드 ---
if "GEMINI_API_KEY" in st.secrets:
    API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    API_KEY = st.sidebar.text_input("Gemini API Key", type="password")

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    st.sidebar.warning("⚠️ 구글 AI 스튜디오에서 발급받은 API Key를 Streamlit Secrets에 넣거나 왼쪽에 입력해 주세요.")

# --- 2단계: 💡 Gemini 멀티모달(Vision) 영수증 번호 및 데이터 추출 엔진 ---
def ask_gemini_vision_parser(file_bytes, mime_type):
    if not API_KEY:
        return "", "", "", 0.0
    try:
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        
        # 파일 바이너리를 Gemini가 이해할 수 있는 멀티모달 형태로 패킹
        file_part = {
            "mime_type": mime_type,
            "data": file_bytes
        }
        
        prompt = """
        너는 독일 세무 회계 및 관세 전표 전문가야. 제공된 영수증/인보이스 문서를 시각적으로 정밀하게 분석해서 아래 4가지 정보만 정확히 찾아내줘.
        
        1. Rechnungsnummer (또는 Belegnummer, Invoice No. 영수증 일련번호)
        2. Rechnungsdatum (발행일, YYYY-MM-DD 형식으로 변환할 것)
        3. Verkäufer (발행 회사명/판매처 이름)
        4. Bruttobetrag (총 합계 금액, 유로화 기호 없이 숫자만 기재, 예: 45.90)

        [출력 포맷 규칙]
        반드시 다른 설명 없이 아래 형식으로만 한 줄씩 출력해줘:
        Beleg_Nr: [번호]
        Datum: [YYYY-MM-DD]
        Vendor: [회사명]
        Total: [금액]

        만약 해당 항목을 도저히 찾을 수 없다면 빈칸으로 비워둬 (예: Beleg_Nr: )
        """
        
        response = model.generate_content([file_part, prompt])
        res_text = response.text
        
        # 결과 파싱
        beleg_nr = ""
        date_str = datetime.now().strftime("%Y-%m-%d")
        vendor = "Unbekannt"
        total_val = 0.0
        
        for line in res_text.split('\n'):
            if line.startswith("Beleg_Nr:"):
                beleg_nr = line.replace("Beleg_Nr:", "").strip()
            elif line.startswith("Datum:"):
                dt = line.replace("Datum:", "").strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", dt): date_str = dt
            elif line.startswith("Vendor:"):
                v = line.replace("Vendor:", "").strip()
                if v: vendor = v
            elif line.startswith("Total:"):
                t = line.replace("Total:", "").strip()
                try: total_val = float(t)
                except: pass
                
        return beleg_nr, date_str, vendor, total_val
    except Exception as e:
        st.sidebar.error(f"❌ Gemini API 오류: {e}")
        return "", datetime.now().strftime("%Y-%m-%d"), "Error", 0.0

# --- 3단계: 콜백 함수 테이블 동적 제어 레이어 ---
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
            
            beleg_no_val = str(master_df.at[row_idx, "Beleg_Nr"]).strip()
            b_suffix = f"_{beleg_no_val}" if beleg_no_val and beleg_no_val.lower() != "none" and beleg_no_val != "" else ""
            
            inv_val = str(master_df.at[row_idx, "Verknüpfte_INV"]).strip()
            inv_suffix = f"_{inv_val}" if inv_val and inv_val.lower() != "none" and inv_val != "" else ""
            
            v_clean = re.sub(r'[\\/*?:"<>|]', '', str(master_df.at[row_idx, "Verkäufer"])).strip()
            date_val = master_df.at[row_idx, "Rechnungsdatum"]
            ext_val = master_df.at[row_idx, "_FileExt"]
            
            master_df.at[row_idx, "DATEV-Dateiname"] = f"{date_val}_{v_clean}_{brutto:.2f}EUR_{z_code}{b_suffix}{inv_suffix}.{ext_val}"
            
        st.session_state.edited_receipts = master_df

# --- 4단계: UI 구동 및 데이터 파이프라인 결합 ---
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
        
        progress_bar = st.progress(0)
        total_files = len(uploaded_files)
        
        with st.spinner("🔮 Gemini Vision AI가 문서를 직접 보고 판독 중..."):
            for idx, uploaded_file in enumerate(uploaded_files):
                file_bytes = uploaded_file.read()
                file_ext = uploaded_file.name.split('.')[-1].lower()
                
                # 파일 확장자에 맞는 MIME Type 매핑
                if file_ext == "pdf": mime_type = "application/pdf"
                elif file_ext in ["jpg", "jpeg"]: mime_type = "image/jpeg"
                else: mime_type = "image/png"
                
                # 💡 핵심: Gemini AI 무료 API 하나로 전체 필드 원샷 추출
                ai_beleg_nr, ai_date, ai_vendor, ai_total = ask_gemini_vision_parser(file_bytes, mime_type)
                
                # 독일 표준 부가세 19% 역산 역추정 계산
                mwst_19 = round(ai_total * 19 / 119, 2)
                vendor_clean = re.sub(r'[\\/*?:"<>|]', '', ai_vendor).strip()
                init_z_code = "BANK" if default_zahlart == "Firmenkonto" else "CC"
                
                b_suffix = f"_{ai_beleg_nr}" if ai_beleg_nr else ""
                proposed_name = f"{ai_date}_{vendor_clean}_{ai_total:.2f}EUR_{init_z_code}{b_suffix}.{file_ext}"
                
                receipt_data.append({
                    "Rechnungsdatum": ai_date, 
                    "Verkäufer": ai_vendor,
                    "Brutto (€)": ai_total, 
                    "MwSt 19% (€)": mwst_19, 
                    "Netto (€)": round(ai_total - mwst_19, 2),
                    "Zahlart": default_zahlart,  
                    "Beleg_Nr": ai_beleg_nr,  # 🎯 AI가 눈으로 보고 직접 찾아낸 리얼 일련번호 기입!
                    "Verknüpfte_INV": "",      
                    "DATEV-Dateiname": proposed_name,
                    "_FileExt": file_ext
                })
                
                # 무료 티어 안정망 4.2초 지연 스로틀링
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
            "Zahlart": st.column_config.SelectboxColumn("Zahlart (결제)", options=["Firmenkonto", "Mastercard"], width="medium", required=True),
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

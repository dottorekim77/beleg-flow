import streamlit as st
import pandas as pd
import io
import os

# 1. 파일 경로 설정
MAPPING_FILE = "user_mapping.csv"

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        return pd.read_csv(MAPPING_FILE)
    else:
        return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "SKR03_코드", "계정과목명"])

def save_mapping(df):
    df.to_csv(MAPPING_FILE, index=False, encoding='utf-8-sig')

# --- 2. [추가] SKR03 및 SKR04 시스템 추천 엔진 데이터베이스 ---
SYSTEM_RECOMMENDATIONS = {
    "wasser": {
        "SKR04": {"code": "6643", "name": "Aufmerksamkeiten"},
        "SKR03": {"code": "4653", "name": "Aufmerksamkeiten"}
    },
    "keks": {
        "SKR04": {"code": "6643", "name": "Aufmerksamkeiten"},
        "SKR03": {"code": "4653", "name": "Aufmerksamkeiten"}
    },
    "mail": {
        "SKR04": {"code": "6830", "name": "EDV-Aufwendungen"},
        "SKR03": {"code": "4925", "name": "Telekommunikation/EDV"}
    },
    "google": {
        "SKR04": {"code": "6830", "name": "EDV-Aufwendungen"},
        "SKR03": {"code": "4925", "name": "Telekommunikation/EDV"}
    },
    "reparatur": {
        "SKR04": {"code": "6335", "name": "Instandhaltung betriebl. Räume"},
        "SKR03": {"code": "4260", "name": "Instandhaltung betriebl. Räume"}
    },
    "dpd": {
        "SKR04": {"code": "4730", "name": "Ausgangsfrachten"},
        "SKR03": {"code": "4730", "name": "Ausgangsfrachten"} #Frachten은 동일 코드 많음
    },
    "dhl": {
        "SKR04": {"code": "4730", "name": "Ausgangsfrachten"},
        "SKR03": {"code": "4730", "name": "Ausgangsfrachten"}
    },
    "steuerberater": {
        "SKR04": {"code": "7210", "name": "Buchführungskosten"},
        "SKR03": {"code": "4955", "name": "Buchführungskosten"}
    },
    "post": {
        "SKR04": {"code": "7100", "name": "Porto"},
        "SKR03": {"code": "4910", "name": "Porto"}
    }
}

# --- 3. 데이터 변환 및 가독성 개선 비즈니스 로직 ---
def process_datev_data(uploaded_file, mapping_df, framework_type):
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file, dtype=str)
    else:
        df = pd.read_excel(uploaded_file, dtype=str)
    
    # 가독성 변환 포맷 서식들
    def fix_date(d):
        if pd.isna(d) or len(str(d)) < 8: return d
        d_str = str(d).strip()
        return f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
    
    def fix_invoice(i):
        if pd.isna(i): return i
        i_str = str(i).strip()
        if i_str.startswith('I'): return f"INV-{i_str[1:]}"
        return i_str
        
    def fix_price(p):
        if pd.isna(p): return 0.0
        try:
            cleaned = str(p).replace(',', '').replace('.', '').strip()
            return float(cleaned) / 100.0
        except: return 0.0

    column_mapping = {
        '날짜': '날짜', 'Buchungstag': '날짜',
        '인보이스번호': '인보이스번호', 'Belegfeld1': '인보이스번호',
        '판매처': '판매처', 'Geschäftspartner': '판매처', 'Kreditor': '판매처',
        '영수증번호': '영수증번호', 'Belegnummer': '영수증번호',
        '가격': '가격', 'Umsatz': '가격',
        'zahlungsart': 'zahlungsart', 'Zahlungsart': 'zahlungsart'
    }
    df = df.rename(columns=column_mapping)
    
    for col in ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart']:
        if col not in df.columns: df[col] = ""
            
    df['날짜'] = df['날짜'].apply(fix_date)
    df['인보이스번호'] = df['인보이스번호'].apply(fix_invoice)
    df['가격_실수'] = df['가격'].apply(fix_price)
    
    # [우선순위 엔진] SKR03 / SKR04 유연 매칭 반영
    def assign_skr(vendor):
        if pd.isna(vendor): return "9999", "Klärungsbedarf"
        v_str = str(vendor).strip().lower()
        
        # 1순위: 사용자 수동 지정 마스터 뒤지기
        target_col = "SKR04_코드" if framework_type == "SKR04" else "SKR03_코드"
        for _, row in mapping_df.iterrows():
            if str(row['판매처_키워드']).lower() in v_str:
                # 사용자가 해당 프레임워크 코드를 누락했을 때의 예외 방어
                code_val = row[target_col] if not pd.isna(row[target_col]) else "9999"
                return str(code_val), str(row['계정과목명'])
                
        # 2순위: 내장 알고리즘 추천 검색
        for key, data in SYSTEM_RECOMMENDATIONS.items():
            if key in v_str:
                return data[framework_type]["code"], data[framework_type]["name"] + " (추천)"
                
        return "9999", "Klärungsbedarf (미지정)"

    df[f'{framework_type}_코드'], df['계정과목명'] = zip(*df['판매처'].apply(assign_skr))
    df['가격'] = df['가격_실수'].apply(lambda x: f"{x:,.2f}")
    
    # 가독성 정렬 순서 확립
    final_cols = ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart', f'{framework_type}_코드', '계정과목명']
    return df[final_cols], df.drop(columns=['가격']).rename(columns={'가격_실수': '가격'})[final_cols]

# --- 4. Streamlit UI 대시보드 ---
st.set_page_config(page_title="DATEV Multi-SKR 변환기", layout="wide")
st.title("🧾 DATEV 가독성 정리 및 SKR03/04 자동 분개 매칭 엔진")

# [핵심 토글 인프라] 글로벌 세무 계정 과목표 선택 스위치
tax_framework = st.sidebar.radio("📋 사용할 세무 과목표(Kostenrahmen) 선택", ["SKR04", "SKR03"], index=0)
st.sidebar.info(f"현재 시스템이 **{tax_framework}** 기준으로 기입 및 추천을 수행합니다.")

tab_main, tab_settings = st.tabs(["📊 데이터 정렬 및 엑셀 출력", f"⚙️ {tax_framework} 거래처 커스텀 설정창"])
user_mapping_df = load_mapping()

# ----------------------------------------------------
# 탭 1: 파일 업로드 및 실시간 변환 피드
# ----------------------------------------------------
with tab_main:
    uploaded_file = st.file_uploader("DATEV 원본 데이터 파일(CSV/Excel) 로드", type=["csv", "xlsx"])
    
    if uploaded_file is not None:
        st.write("---")
        display_df, excel_df = process_datev_data(uploaded_file, user_mapping_df, tax_framework)
        st.dataframe(display_df, use_container_width=True)
        
        # 엑셀 바이너리 포맷 레이아웃 가독성 제어
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            excel_df.to_excel(writer, index=False, sheet_name=f'DATEV_{tax_framework}_정리')
            workbook  = writer.book
            worksheet = writer.sheets[f'DATEV_{tax_framework}_정리']
            money_format = workbook.add_format({'num_format': '#,##0.00'})
            
            worksheet.set_column('E:E', 15, money_format) # 가격 열 포맷 규칙 지정
            worksheet.set_column('A:D', 18)
            worksheet.set_column('F:H', 22)
            
        excel_data = output.getvalue()
        st.write("---")
        st.download_button(
            label=f"💾 가독성 개선 엑셀 파일({tax_framework} 매칭 완료).xlsx 다운로드",
            data=excel_data,
            file_name=f"DATEV_{tax_framework}_Formated_{uploaded_file.name.split('.')[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ----------------------------------------------------
# 탭 2: 멀티 SKR 대응 설정창 인프라
# ----------------------------------------------------
with tab_settings:
    st.subheader(f"⚙️ 단골 거래처용 {tax_framework} 매칭 셋업")
    st.write(f"여기에 입력해 둔 거래처는 알고리즘보다 앞서 마스터 테이블 가중치를 우선 적용받습니다.")
    
    with st.form("hybrid_vendor_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            v_key = st.text_input("거래처 이름/키워드 기입", placeholder="예: DPD, DHL")
        with col2:
            c_key = st.text_input(f"우선순위 {tax_framework} 코드 번호", placeholder="예: 4730")
        with col3:
            n_key = st.text_input("계정과목 레이블 이름 명칭", placeholder="예: Ausgangsfrachten")
            
        submit = st.form_submit_button("➕ 규칙 장부 데이터베이스에 추가 저장")
        
        if submit:
            if v_key and c_key:
                # SKR03, SKR04 열 유연 대응 동적 프레임 생성
                new_row = {
                    "판매처_키워드": v_key.strip(),
                    "계정과목명": n_key.strip(),
                    "SKR04_코드": c_key.strip() if tax_framework == "SKR04" else "",
                    "SKR03_코드": c_key.strip() if tax_framework == "SKR03" else ""
                }
                new_df = pd.DataFrame([new_row])
                user_mapping_df = pd.concat([user_mapping_df, new_df], ignore_index=True)
                save_mapping(user_mapping_df)
                st.success(f"✔️ 규칙 데이터 세이브 성공: {v_key} ➡️ {c_key}")
            else:
                st.error("⚠️ 필수 항목 누락됨.")

    st.write("---")
    st.subheader("📋 통합 마스터 매칭 테이블 원본 데이터 편집")
    
    if not user_mapping_df.empty:
        # 데이터프레임 구조 보존 및 에디터 로드
        updated_map = st.data_editor(user_mapping_df, num_rows="dynamic", use_container_width=True, key="hybrid_editor")
        if st.button("💾 편집된 전체 테이블 저장"):
            save_mapping(updated_map)
            st.rerun()
    else:
        st.info("현재 로컬에 누적 보관된 규칙이 비어 있습니다.")

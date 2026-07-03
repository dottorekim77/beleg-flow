import streamlit as st
import pandas as pd
import io
import os

# 1. 파일 경로 설정 (사용자 지정 단골 거래처 매칭 데이터 저장용)
MAPPING_FILE = "user_mapping.csv"

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        return pd.read_csv(MAPPING_FILE)
    else:
        return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "계정과목명"])

def save_mapping(df):
    df.to_csv(MAPPING_FILE, index=False, encoding='utf-8-sig')

# --- 2. 시스템 기본 추천 알고리즘 딕셔너리 ---
SYSTEM_RECOMMENDATIONS = {
    "wasser": {"code": "6643", "name": "Aufmerksamkeiten (추천)"},
    "keks": {"code": "6643", "name": "Aufmerksamkeiten (추천)"},
    "mail": {"code": "6830", "name": "EDV-Aufwendungen (추천)"},
    "google": {"code": "6830", "name": "EDV-Aufwendungen (추천)"},
    "reparatur": {"code": "6335", "name": "Instandhaltung (추천)"}
}

# --- 3. 핵심 비즈니스 로직: 데이터 변환 및 가독성 개선 함수 ---
def process_datev_data(uploaded_file, mapping_df):
    # 파일 읽기 (CSV 또는 Excel 대응)
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file, dtype=str)
    else:
        df = pd.read_excel(uploaded_file, dtype=str)
    
    # [요구사항 1] 날짜 포맷 변경 (YYYYMMDD -> YYYY-MM-DD)
    def fix_date(d):
        if pd.isna(d) or len(str(d)) < 8: return d
        d_str = str(d).strip()
        return f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
    
    # [요구사항 2] 인보이스 기호 명확화 (I -> INV-)
    def fix_invoice(i):
        if pd.isna(i): return i
        i_str = str(i).strip()
        if i_str.startswith('I'):
            return f"INV-{i_str[1:]}"
        return i_str
        
    # [요구사항 3] 금액 천 단위 및 소수점 처리 (DATEV 센트 단위를 Float 유로 단위로)
    def fix_price(p):
        if pd.isna(p): return 0.0
        try:
            # 문자열 내 쉼표/공백 제거 후 센트 단위를 유로로 환산
            cleaned = str(p).replace(',', '').replace('.', '').strip()
            return float(cleaned) / 100.0
        except:
            return 0.0

    # 원본 파일의 컬럼명에 맞춰 맵핑 필요 (여기선 요청하신 기본 컬럼명 매칭을 가정합니다)
    # 실제 DATEV 파일 컬럼명에 따라 ['Buchungstag', 'Belegfeld1', 'Geschäftspartner', 'Beleglink', 'Umsatz', 'Zahlungsart'] 등으로 매칭을 변경할 수 있습니다.
    column_mapping = {
        '날짜': '날짜', 'Buchungstag': '날짜',
        '인보이스번호': '인보이스번호', 'Belegfeld1': '인보이스번호',
        '판매처': '판매처', 'Geschäftspartner': '판매처', 'Kreditor': '판매처',
        '영수증번호': '영수증번호', 'Belegnummer': '영수증번호',
        '가격': '가격', 'Umsatz': '가격',
        'zahlungsart': 'zahlungsart', 'Zahlungsart': 'zahlungsart'
    }
    
    df = df.rename(columns=column_mapping)
    
    # 누락된 필수 컬럼이 있을 경우 방어 코드
    for col in ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart']:
        if col not in df.columns:
            df[col] = ""
            
    df['날짜'] = df['날짜'].apply(fix_date)
    df['인보이스번호'] = df['인보이스번호'].apply(fix_invoice)
    df['가격_실수'] = df['가격'].apply(fix_price) # 계산/포맷용 원본 숫자 데이터
    
    # [설정창 연동] 우선순위 기준 Kostenrahmen 자동 매칭
    def assign_skr(vendor):
        if pd.isna(vendor): return "9999", "Klärungsbedarf (미지정)"
        v_str = str(vendor).strip().lower()
        
        # 1순위: 사용자가 수동 저장한 규칙 검색
        for _, row in mapping_df.iterrows():
            if str(row['판매처_키워드']).lower() in v_str:
                return str(row['SKR04_코드']), str(row['계정과목명'])
                
        # 2순위: 시스템 기본 추천 알고리즘 규칙 검색
        for key, data in SYSTEM_RECOMMENDATIONS.items():
            if key in v_str:
                return data["code"], data["name"]
                
        # 3순위: 매칭 실패 시
        return "9999", "Klärungsbedarf (미지정 계정)"

    df['SKR04_코드'], df['계정과목명'] = zip(*df['판매처'].apply(assign_skr))
    
    # 화면 표시 및 엑셀 출력을 위한 천 단위 콤마 포맷팅화 문자열 생성
    df['가격'] = df['가격_실수'].apply(lambda x: f"{x:,.2f}")
    
    # [요구사항 4] 정확한 컬럼 순서 지정 및 재배치 흐름 직관화
    final_cols = ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart', 'SKR04_코드', '계정과목명']
    return df[final_cols], df.drop(columns=['가격']).rename(columns={'가격_실수': '가격'})[final_cols] # 엑셀 출력용 포맷팅 포함 데이터 분리

# --- 4. Streamlit UI 메인 화면 구성 ---
st.set_page_config(page_title="DATEV 변환 툴", layout="wide")
st.title("🧾 DATEV Digitale Belege 가독성 변환 & SKR04 자동 기입 시스템")

# 탭 구조 정의 (작업 화면과 설정창 완벽 분리)
tab_main, tab_settings = st.tabs(["📊 데이터 변환 및 다운로드", "⚙️ 거래처별 SKR04 설정창"])

# 매스터 데이터 로드
user_mapping_df = load_mapping()

# ----------------------------------------------------
# 탭 1: 데이터 변환 및 엑셀 내보내기 기능
# ----------------------------------------------------
with tab_main:
    st.subheader("파일 업로드")
    uploaded_file = st.file_uploader("DATEV 원본 데이터 파일(CSV 또는 Excel)을 선택하세요.", type=["csv", "xlsx"])
    
    if uploaded_file is not None:
        st.write("---")
        st.subheader("🔄 실시간 변환 결과 미리보기")
        
        # 데이터 처리 실행
        display_df, excel_df = process_datev_data(uploaded_file, user_mapping_df)
        
        # 눈이 편안한 표 형태로 웹화면에 렌더링
        st.dataframe(display_df, use_container_width=True)
        
        # 엑셀 다운로드 파일 생성 (인메모리 바이너리 스트림 활용)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            excel_df.to_excel(writer, index=False, sheet_name='DATEV_정리파일')
            
            # 💡 엑셀 전용 천 단위 구분 기호 기입 서식 자동 적용 규칙 추가
            workbook  = writer.book
            worksheet = writer.sheets['DATEV_정리파일']
            money_format = workbook.add_format({'num_format': '#,##0.00'})
            
            # 가격(E열) 전체에 엑셀 회계 서식 강제 적용
            worksheet.set_column('E:E', 15, money_format)
            # 열 너비 가독성 확장
            worksheet.set_column('A:D', 18)
            worksheet.set_column('F:H', 22)
            
        excel_data = output.getvalue()
        
        st.write("---")
        st.download_button(
            label="💾 가독성 개선된 정렬 엑셀 파일(.xlsx) 다운로드",
            data=excel_data,
            file_name=f"DATEV_Formated_{uploaded_file.name.split('.')[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ----------------------------------------------------
# 탭 2: 사용자 지정 단골 거래처 매칭 설정창
# ----------------------------------------------------
with tab_settings:
    st.subheader("⚙️ 단골 거래처 마스터 데이터 수동 지정")
    st.write("대부분 반복되는 단골 거래처의 규칙을 기입해 두면, 시스템이 원본 파일에서 해당 키워드를 찾아 입력하신 코드를 최우선으로 기입합니다.")
    
    # 폼 레이아웃을 이용한 기입창 구현
    with st.form("vendor_rule_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            v_key = st.text_input("판매처 키워드 등록", placeholder="예: DPD, DHL, Steuerberater")
        with c2:
            c_key = st.text_input("우선순위 SKR04 코드 지정", placeholder="예: 4730, 7210")
        with c3:
            n_key = st.text_input("계정과목명 명칭", placeholder="예: Ausgangsfrachten")
            
        submit = st.form_submit_button("➕ 매칭 규칙 저장하기")
        
        if submit:
            if v_key and c_key:
                new_data = pd.DataFrame([{"판매처_키워드": v_key.strip(), "SKR04_코드": c_key.strip(), "계정과목명": n_key.strip()}])
                user_mapping_df = pd.concat([user_mapping_df, new_data], ignore_index=True)
                save_mapping(user_mapping_df)
                st.success(f"✔️ 규칙 추가 완료: {v_key} ➡️ {c_key}")
            else:
                st.error("⚠️ 판매처 키워드와 SKR04 코드는 필수로 채워주셔야 합니다.")
                
    st.write("---")
    st.subheader("📋 현재 저장된 수동 매칭 데이터 리스트")
    st.write("수정이 필요하면 표 안을 더블클릭하여 편집하고 아래 저장 버튼을 누르세요. 행을 선택하고 Del 키를 누르면 행 삭제가 가능합니다.")
    
    if not user_mapping_df.empty:
        # st.data_editor를 통한 인라인 수정 환경 제공
        updated_mapping_df = st.data_editor(user_mapping_df, num_rows="dynamic", use_container_width=True, key="editor_view")
        
        if st.button("💾 변경사항 마스터 데이터에 반영"):
            save_mapping(updated_mapping_df)
            st.rerun()
    else:
        st.info("현재 수동 지정된 규칙이 존재하지 않습니다. 규칙을 등록해 주세요.")

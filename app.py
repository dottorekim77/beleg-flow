import streamlit as st
import pandas as pd
import io
import os

# ==========================================
# 1. 파일 경로 및 데이터베이스 정의
# ==========================================
MAPPING_FILE = "user_mapping.csv"

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        return pd.read_csv(MAPPING_FILE)
    return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "SKR03_코드", "계정과목명"])

def save_mapping(df):
    df.to_csv(MAPPING_FILE, index=False, encoding='utf-8-sig')

# 시스템 기본 추천 엔진 데이터베이스 (단골 지정이 없을 때 작동)
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
        "SKR03": {"code": "4730", "name": "Ausgangsfrachten"}
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

# ==========================================
# 2. 원래 있었던 기능들을 유지하기 위한 기본 파싱 틀 (예시)
# ==========================================
def original_parsing_logic(uploaded_file):
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file, dtype=str)
    else:
        df = pd.read_excel(uploaded_file, dtype=str)
    
    # DATEV 표준 필드명을 유연하게 매칭하기 위한 기존 컬럼 맵핑 틀
    column_mapping = {
        'Buchungstag': '날짜', 'Datum': '날짜',
        'Belegfeld1': '인보이스번호', 'Rechnungsnummer': '인보이스번호',
        'Geschäftspartner': '판매처', 'Kreditor': '판매처', 'Name': '판매처',
        'Belegnummer': '영수증번호', 'Id': '영수증번호',
        'Umsatz': '가격', 'Betrag': '가격',
        'Zahlungsart': 'zahlungsart'
    }
    df = df.rename(columns=column_mapping)
    
    # 필수 컬럼 누락 방지를 위한 기본 틀 유지
    required_cols = ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart']
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
            
    return df

# ==========================================
# 3. [신규 추가] 가독성 개선 및 SKR 코드 주입 모듈
# ==========================================
def apply_readability_and_skr(df, framework_type):
    mapping_df = load_mapping()
    
    # 기능 1: 날짜 포맷 변경 (YYYYMMDD -> YYYY-MM-DD)
    def fix_date(d):
        if pd.isna(d) or len(str(d)) < 8: return d
        d_str = str(d).strip()
        return f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
    
    # 기능 2: 인보이스 기호 명확화 (I -> INV-)
    def fix_invoice(i):
        if pd.isna(i): return i
        i_str = str(i).strip()
        return f"INV-{i_str[1:]}" if i_str.startswith('I') else i_str
        
    # 기능 3: 금액 천 단위 및 소수점 처리 준비 (센트 -> 유로 변환 및 Float 실수화)
    def fix_price(p):
        if pd.isna(p): return 0.0
        try:
            cleaned = str(p).replace(',', '').replace('.', '').strip()
            return float(cleaned) / 100.0
        except:
            return 0.0

    # 기능 4: 수동 저장 설정 우선 순위 SKR 매칭 엔진
    def assign_skr(vendor):
        if pd.isna(vendor): return "9999", "Klärungsbedarf"
        v_str = str(vendor).strip().lower()
        
        # 1순위: 사용자가 수동 저장창에서 입력해 둔 데이터 검색
        target_col = "SKR04_코드" if framework_type == "SKR04" else "SKR03_코드"
        for _, row in mapping_df.iterrows():
            if str(row['판매처_키워드']).lower() in v_str:
                code_val = row[target_col] if not pd.isna(row[target_col]) else "9999"
                return str(code_val), str(row['계정과목명'])
                
        # 2순위: 사용자 리스트에 없으면 내장 알고리즘 시스템 추천 작동
        for key, data in SYSTEM_RECOMMENDATIONS.items():
            if key in v_str:
                return data[framework_type]["code"], data[framework_type]["name"] + " (추천)"
                
        return "9999", "Klärungsbedarf (미지정)"

    # 데이터 서식 변형 적용
    df['날짜'] = df['날짜'].apply(fix_date)
    df['인보이스번호'] = df['인보이스번호'].apply(fix_invoice)
    df['가격_실수'] = df['가격'].apply(fix_price)
    
    # 과목표 선택에 맞는 동적 컬럼 생성 및 맵핑
    code_col_name = f"{framework_type}_코드"
    df[code_col_name], df['계정과목명'] = zip(*df['판매처'].apply(assign_skr))
    
    # 웹 화면 출력용 천 단위 콤마 포맷팅화 스트링 생성
    df['가격'] = df['가격_실수'].apply(lambda x: f"{x:,.2f}")
    
    # 기능 5: 정확한 컬럼 순서 지정 왼쪽 -> 오른쪽 흐름 정렬 고정
    final_cols = ['날짜', '인보이스번호', '판매처', '영수증번호', '가격', 'zahlungsart', code_col_name, '계정과목명']
    
    # 웹 화면 표시용 df와 엑셀 내보내기용 df(가격 데이터를 숫자로 보존)를 분리하여 반환
    display_df = df[final_cols]
    excel_df = df.drop(columns=['가격']).rename(columns={'가격_실수': '가격'})[final_cols]
    
    return display_df, excel_df

# ==========================================
# 4. Streamlit UI 대시보드 (기본 틀 보존 + 탭 분리)
# ==========================================
st.set_page_config(page_title="DATEV 통합 관리 솔루션", layout="wide")
st.title("🧾 DATEV Digitale Belege 가독성 정렬 및 SKR03/04 통합 매칭 시스템")

# 사이드바에서 글로벌 세무 프레임워크 스위칭 단추 제공 (원래 기능을 방해하지 않음)
tax_framework = st.sidebar.radio("📋 사용할 세무 과목표(Kostenrahmen) 선택", ["SKR04", "SKR03"], index=0)
st.sidebar.info(f"현재 선택된 과목표: **{tax_framework}**")

# 기존 메인 화면의 워크플로우를 보존하기 위해 탭으로 공간만 분리
tab_main, tab_settings = st.tabs(["📊 DATEV 파일 처리 및 정렬", f"⚙️ {tax_framework} 거래처 수동 지정 설정창"])

user_mapping_df = load_mapping()

# ----------------------------------------------------
# 탭 1: 원래 존재하던 작업 화면 (가독성+SKR 기능 연결)
# ----------------------------------------------------
with tab_main:
    st.subheader("파일 업로드")
    uploaded_file = st.file_uploader("DATEV 원본 데이터 파일(CSV 또는 Excel)을 선택하세요.", type=["csv", "xlsx"])
    
    if uploaded_file is not None:
        st.write("---")
        st.subheader("🔄 실시간 정렬 및 변환 결과")
        
        # Step 1: 원래 있었던 파싱 핵심 로직을 그대로 태워 기본 틀을 유지합니다.
        parsed_df = original_parsing_logic(uploaded_file)
        
        # Step 2: 파싱이 완료된 기본 데이터프레임을 깨뜨리지 않고 가독성 파이프라인에 통과시킵니다.
        display_df, excel_df = apply_readability_and_skr(parsed_df, tax_framework)
        
        # 화면에 가독성이 높은 표 형태로 출력
        st.dataframe(display_df, use_container_width=True)
        
        # 엑셀 다운로드 바이너리 파일 스트림 빌드
        output = io.BytesIO()
        sheet_name_str = f"DATEV_{tax_framework}_정리"
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            excel_df.to_excel(writer, index=False, sheet_name=sheet_name_str)
            
            workbook  = writer.book
            worksheet = writer.sheets[sheet_name_str]
            
            # 가격(E열)에 엑셀 전용 천 단위 구분 기호 및 소수점 서식을 명확히 입힘
            money_format = workbook.add_format({'num_format': '#,##0.00'})
            worksheet.set_column('E:E', 15, money_format)
            
            # 전체 너비 가독성 확보
            worksheet.set_column('A:D', 18)
            worksheet.set_column('F:H', 22)
            
        excel_data = output.getvalue()
        st.write("---")
        st.download_button(
            label=f"💾 가독성 개선된 {tax_framework} 엑셀 파일 다운로드",
            data=excel_data,
            file_name=f"DATEV_{tax_framework}_Formated_{uploaded_file.name.split('.')[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ----------------------------------------------------
# 탭 2: 단골 거래처를 수동으로 입력하고 저장할 수 있는 설정창
# ----------------------------------------------------
with tab_settings:
    st.subheader(f"⚙️ {tax_framework} 커스텀 단골 거래처 규칙 기입")
    st.write("자주 사용하는 거래처명과 전용 코드를 기입해 두면 파일 업로드 시 시스템 알고리즘 추천보다 항상 최우선으로 적용됩니다.")
    
    with st.form("hybrid_vendor_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            v_key = st.text_input("거래처 이름/키워드 기입", placeholder="예: DPD, DHL, Steuerberater")
        with col2:
            c_key = st.text_input(f"우선순위 {tax_framework} 코드 번호", placeholder="예: 4730, 7210")
        with col3:
            n_key = st.text_input("계정과목 레이블 이름 명칭", placeholder="예: Ausgangsfrachten")
            
        submit = st.form_submit_button("➕ 매칭 규칙 장부에 저장")
        
        if submit:
            if v_key and c_key:
                new_row = {
                    "판매처_키워드": v_key.strip(),
                    "계정과목명": n_key.strip(),
                    "SKR04_코드": c_key.strip() if tax_framework == "SKR04" else "",
                    "SKR03_코드": c_key.strip() if tax_framework == "SKR03" else ""
                }
                user_mapping_df = pd.concat([user_mapping_df, pd.DataFrame([new_row])], ignore_index=True)
                save_mapping(user_mapping_df)
                st.success(f"✔️ 규칙 데이터베이스 세이브 성공: {v_key} ➡️ {c_key}")
                st.rerun()
            else:
                st.error("⚠️ 거래처 키워드와 코드는 필수로 입력해야 합니다.")

    st.write("---")
    st.subheader("📋 전체 저장된 수동 매칭 마스터 테이블 원본 데이터 편집")
    st.write("표 내부를 더블클릭하여 바로 값을 수정할 수 있으며, 행을 선택해 행 추가/삭제를 간편하게 할 수 있습니다.")
    
    if not user_mapping_df.empty:
        updated_map = st.data_editor(user_mapping_df, num_rows="dynamic", use_container_width=True, key="hybrid_editor")
        if st.button("💾 편집된 전체 테이블 로컬 파일에 최종 저장"):
            save_mapping(updated_map)
            st.toast("모든 규칙이 성공적으로 영구 저장되었습니다!")
            st.rerun()
    else:
        st.info("현재 저장된 수동 매칭 규칙이 없습니다. 위 입력 폼에서 단골 거래처를 등록해 주세요.")

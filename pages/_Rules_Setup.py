import os
import pandas as pd
import streamlit as st

st.set_page_config(page_title="단골 거래처 지정 설정창", page_icon="⚙️", layout="wide")
st.title("⚙️ 단골 거래처 수동 지정 마스터 설정창")
st.caption("여기서 규칙을 수정하거나 추가할 때는 메인 화면의 대용량 파일 파싱 엔진이 간섭하지 않으므로 병목 현상과 튕김이 완벽하게 차단됩니다.")

MAPPING_FILE = "user_mapping.csv"

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        try: return pd.read_csv(MAPPING_FILE)
        except: pass
    return pd.DataFrame(columns=["판매처_키워드", "SKR04_코드", "SKR03_코드", "계정과목명"])

def save_mapping(df):
    df.to_csv(MAPPING_FILE, index=False, encoding='utf-8-sig')

if "mapping_data" not in st.session_state:
    st.session_state.mapping_data = load_mapping()

# 1. 수동 규칙 추가 폼
with st.form("user_custom_vendor_form", clear_on_submit=True):
    col1, col2, col3, col4 = st.columns(4)
    with col1: v_key = st.text_input("거래처 키워드 (예: DHL, Post)")
    with col2: skr03_c = st.text_input("SKR03 코드")
    with col3: skr04_c = st.text_input("SKR04 코드")
    with col4: n_key = st.text_input("계정과목명 레이블 명칭")
    
    if st.form_submit_button("➕ 매칭 규칙 즉시 추가"):
        if v_key.strip():
            new_rule = {
                "판매처_키워드": v_key.strip(), "계정과목명": n_key.strip() if n_key.strip() else "User Defined",
                "SKR03_코드": skr03_c.strip(), "SKR04_코드": skr04_c.strip()
            }
            st.session_state.mapping_data = pd.concat([st.session_state.mapping_data, pd.DataFrame([new_rule])], ignore_index=True)
            save_mapping(st.session_state.mapping_data)
            st.success("✔️ 리스트에 추가되었습니다! 테이블에서 최종 저장을 눌러주세요.")
        else:
            st.error("⚠️ 키워드는 필수입니다.")

st.write("---")
st.subheader("📋 전체 마스터 룰 관리 테이블")

# 2. 버퍼 차단 데이터 에디터 (여기서 수정 시 절대 튕기지 않음)
updated_df = st.data_editor(
    st.session_state.mapping_data, 
    num_rows="dynamic", 
    use_container_width=True,
    key="isolated_rule_editor"
)

# 3. 저장 버튼 먹통 방지 안전 바인딩
if st.button("💾 변경사항 최종 저장하기", type="primary"):
    st.session_state.mapping_data = updated_df
    save_mapping(updated_df)
    st.toast("모든 데이터 규칙이 성공적으로 영구 저장되었습니다!")

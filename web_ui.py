"""
채팅 UI (Web Frontend)
=======================
블로그 다이어그램에서 맨 위 시안색 박스.
- Streamlit으로 채팅 인터페이스 제공
- Cognito로 사용자 로그인 → 토큰 발급
- 코디네이션 AI(A2A 서버)에 요청 전송

[블로그 매핑]
- 채팅 UI        → 이 파일
- Cognito (인증) → 사용자 로그인 시 토큰 발급
- 코디네이션 AI  → A2A 프로토콜로 호출

실행: streamlit run web_ui.py
"""

import streamlit as st
import boto3
import requests

# =============================================================================
# 설정
# =============================================================================

ssm = boto3.client("ssm")
REGION = boto3.Session().region_name
PARAM_PREFIX = "/app/multiagent/blog"

# 코디네이션 AI의 A2A 엔드포인트 (setup.sh에서 배포 후 SSM에 저장됨)
ORCHESTRATOR_URL = ssm.get_parameter(Name=f"{PARAM_PREFIX}/agent_orchestrator_url")["Parameter"]["Value"]

# Cognito 설정 (사용자 인증용)
COGNITO_POOL_ID = ssm.get_parameter(Name=f"{PARAM_PREFIX}/cognito_pool_id")["Parameter"]["Value"]

# =============================================================================
# 페이지 설정
# =============================================================================

st.set_page_config(page_title="멀티에이전트 고객지원", page_icon="🤖", layout="centered")
st.title("🤖 멀티에이전트 고객지원")
st.caption("코디네이션 AI가 적절한 전문가에게 연결해드립니다")

# =============================================================================
# 세션 상태
# =============================================================================

if "messages" not in st.session_state:
    st.session_state.messages = []
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "bearer_token" not in st.session_state:
    st.session_state.bearer_token = None

# =============================================================================
# Cognito 로그인
# =============================================================================


def login(username: str, password: str) -> bool:
    """Cognito로 사용자 인증 → Bearer Token 발급"""
    cognito = boto3.client("cognito-idp", region_name=REGION)
    client_id = ssm.get_parameter(Name=f"{PARAM_PREFIX}/cognito_ui_client_id")["Parameter"]["Value"]

    try:
        response = cognito.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        st.session_state.bearer_token = response["AuthenticationResult"]["AccessToken"]
        st.session_state.authenticated = True
        return True
    except Exception as e:
        st.error(f"로그인 실패: {e}")
        return False


# =============================================================================
# 코디네이션 AI 호출 (A2A)
# =============================================================================


def send_message(user_input: str) -> str:
    """코디네이션 AI에 Bearer Token으로 메시지 전송"""
    import json as json_mod

    body = json_mod.dumps({"prompt": user_input})

    response = requests.post(
        ORCHESTRATOR_URL,
        headers={
            "Authorization": f"Bearer {st.session_state.bearer_token}",
            "Content-Type": "application/json",
        },
        data=body,
        timeout=300,
    )

    try:
        result = response.json()
        if isinstance(result, str):
            return result
        return result.get("text", result.get("result", str(result)))
    except Exception:
        return f"오류: {response.status_code} - {response.text[:500]}"


# =============================================================================
# UI
# =============================================================================

# 로그인 화면
if not st.session_state.authenticated:
    st.markdown("---")
    st.subheader("🔐 로그인")
    with st.form("login_form"):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")
        if submitted:
            login(username, password)
            st.rerun()
else:
    # 채팅 화면
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if user_input := st.chat_input("질문을 입력하세요..."):
        # 사용자 메시지 표시
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.write(user_input)

        # 코디네이션 AI 호출
        with st.chat_message("assistant"):
            with st.spinner("전문가 AI에게 연결 중..."):
                response = send_message(user_input)
            st.write(response)
        st.session_state.messages.append({"role": "assistant", "content": response})

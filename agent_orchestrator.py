"""코디네이션 AI (Orchestrator)
- 고객 질문을 분석 → 적절한 전문가 AI에 A2A로 라우팅
- HTTP 프로토콜 (BedrockAgentCoreApp)
- requestHeaderAllowlist로 전달받은 caller의 Bearer token을 그대로 전문가에게 전달
"""

import os
import json
import re
import uuid
import logging
import boto3
import requests as req_lib
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 리전: AgentCore Runtime이 주입하는 AWS_REGION을 사용합니다(setup.sh가 배포 리전을 전달).
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

# 모델: 라우팅 판단과 도구 호출을 위해 tool use가 안정적인 모델을 사용합니다.
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# =============================================================================
# 전문가 AI URL 조회 (SSM)
# =============================================================================

ssm = boto3.client("ssm", region_name=REGION)
PREFIX = "/app/multiagent/blog"


def get_param(name):
    return ssm.get_parameter(Name=f"{PREFIX}/{name}", WithDecryption=True)["Parameter"]["Value"]


def get_agent_url(agent_name: str) -> str:
    """전문가 AI의 Runtime URL을 요청 시점에 SSM에서 읽습니다.

    모듈 로드 시 한 번만 읽으면, 전문가 AI를 재배포해서 Runtime ID가 바뀌었을 때
    이미 떠 있는 컨테이너가 옛 URL을 계속 호출해 404가 발생합니다.
    """
    return get_param(f"{agent_name}_url")


# =============================================================================
# A2A 호출 — caller의 token을 전달 (requestHeaderAllowlist)
# =============================================================================

# 현재 요청의 token을 저장할 context
_current_token = {"value": ""}


def call_a2a_agent(agent_url: str, message: str) -> str:
    """A2A JSON-RPC로 전문가 Agent에 메시지 전송"""
    token = _current_token["value"]
    logger.info(f"A2A 호출: url=...{agent_url[-45:]}, token_len={len(token)}")

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": message}],
            }
        },
    }
    resp = req_lib.post(
        agent_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    logger.info(f"A2A 응답: status={resp.status_code}")
    resp.raise_for_status()
    body = resp.json()

    if "error" in body:
        raise RuntimeError(f"A2A 오류 응답: {body['error']}")

    result = body.get("result")
    if not result:
        raise RuntimeError(f"A2A 응답에 result가 없습니다: {body}")

    # Task 상태 확인 — HTTP 200이라도 task가 failed로 끝날 수 있습니다.
    # 이 경우 history에는 도중에 끊긴 부분 텍스트만 남으므로, 성공으로 오인하면 안 됩니다.
    state = result.get("status", {}).get("state")
    logger.info(f"A2A task state={state}")
    if state and state != "completed":
        raise RuntimeError(f"전문가 AI 작업이 '{state}' 상태로 종료되었습니다.")

    # 1) artifacts (완료된 task의 최종 결과)
    texts = [
        part["text"]
        for artifact in result.get("artifacts") or []
        for part in artifact.get("parts", [])
        if part.get("kind") == "text"
    ]
    if texts:
        return "\n".join(texts)

    # 2) history의 agent 메시지 이어붙이기 (StrandsA2AExecutor는 토큰 단위로 쪼개서 보냄)
    agent_parts = [
        part["text"]
        for msg in result.get("history") or []
        if msg.get("role") == "agent"
        for part in msg.get("parts", [])
        if part.get("kind") == "text"
    ]
    if agent_parts:
        full_text = re.sub(
            r"<thinking>.*?</thinking>", "", "".join(agent_parts), flags=re.DOTALL
        ).strip()
        if full_text:
            return full_text

    raise RuntimeError(f"전문가 AI 응답에서 텍스트를 찾지 못했습니다. state={state}")


@tool(name="ask_consultant", description="상담사 AI에게 질문합니다. 제품 스펙, 가격, 보증, 반품 정책을 조회할 때 사용합니다.")
def ask_consultant(question: str) -> str:
    """상담사 AI에게 질문 전달"""
    try:
        return call_a2a_agent(get_agent_url("agent_consultant"), question)
    except Exception as e:
        logger.error(f"[ask_consultant] 실패: {type(e).__name__}: {e}")
        return f"상담사 AI 호출 실패: {e}"


@tool(name="ask_technician", description="기술자 AI에게 질문합니다. 과열, 배터리, 블루투스 등 기술 문제 진단에 사용합니다.")
def ask_technician(question: str) -> str:
    """기술자 AI에게 질문 전달"""
    try:
        return call_a2a_agent(get_agent_url("agent_technician"), question)
    except Exception as e:
        logger.error(f"[ask_technician] 실패: {type(e).__name__}: {e}")
        return f"기술자 AI 호출 실패: {e}"


# =============================================================================
# Agent & Runtime
# =============================================================================

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)

SYSTEM_PROMPT = """당신은 고객 지원 코디네이터입니다.
고객 질문을 분석해서 적절한 전문가 AI에게 라우팅하세요.

사용 가능한 도구:
- ask_consultant: 제품 스펙, 가격, 보증, 반품 정책 문의 (Galaxy S25 Ultra, Galaxy Buds3 Pro, Galaxy Tab S10 Ultra)
- ask_technician: 과열, 배터리, 블루투스 등 기술 문제 진단

절차:
1. 고객 질문을 분석합니다.
2. 적절한 도구(ask_consultant 또는 ask_technician)를 호출합니다.
3. 전문가의 응답을 고객에게 전달합니다.

항상 한국어로 응답하세요."""

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context: RequestContext):
    """요청 처리 — caller의 Authorization 헤더를 전문가 호출에 전달"""
    # requestHeaderAllowlist로 전달된 Authorization 헤더 추출
    auth_header = ""
    if hasattr(context, "request_headers") and context.request_headers:
        auth_header = context.request_headers.get("authorization", "") or context.request_headers.get("Authorization", "")
    
    # "Bearer " 접두어 제거 후 저장
    if auth_header.startswith("Bearer "):
        _current_token["value"] = auth_header[7:]
    else:
        _current_token["value"] = auth_header
    
    logger.info(f"Token received: len={len(_current_token['value'])}")

    orchestrator = Agent(
        model=model,
        tools=[ask_consultant, ask_technician],
        system_prompt=SYSTEM_PROMPT,
    )

    user_input = payload.get("prompt", "")
    response = orchestrator(user_input)
    return response.message["content"][0]["text"]

if __name__ == "__main__":
    app.run()

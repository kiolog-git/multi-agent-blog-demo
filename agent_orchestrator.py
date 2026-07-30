"""코디네이션 AI (Orchestrator)"""

import os
import boto3
from strands import Agent
from strands.models import BedrockModel
from strands_tools.a2a_client import A2AClientToolProvider
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

# 전문가 AI들의 Runtime URL 가져오기
def get_agent_urls():
    """SSM에서 consultant/technician URL을 가져옴"""
    try:
        ssm = boto3.client("ssm", region_name="us-east-1")
        consultant_url = ssm.get_parameter(Name="/app/multiagent/blog/agent_consultant_url")["Parameter"]["Value"]
        technician_url = ssm.get_parameter(Name="/app/multiagent/blog/agent_technician_url")["Parameter"]["Value"]
        # URL 끝에 / 필요
        if not consultant_url.endswith("/"):
            consultant_url += "/"
        if not technician_url.endswith("/"):
            technician_url += "/"
        return [consultant_url, technician_url]
    except Exception as e:
        print(f"SSM 읽기 실패: {e}")
        return []

AGENT_URLS = get_agent_urls()
print(f"Agent URLs: {AGENT_URLS}")

# 모델 초기화
model = BedrockModel(model_id="us.amazon.nova-pro-v1:0", region_name="us-east-1")

SYSTEM_PROMPT = """당신은 고객 지원 코디네이터입니다.
고객 질문을 분석해서 적절한 전문가 AI에게 라우팅하세요.

사용 가능한 전문가:
- consultant: 제품 스펙, 가격, 보증, 반품 정책 (Galaxy S25 Ultra, Galaxy Buds3 Pro, Galaxy Tab S10 Ultra)
- technician: 과열, 배터리, 블루투스 등 기술 문제 진단

절차:
1. a2a_send_message로 적절한 전문가에게 질문을 전달하세요.
2. 전문가의 응답을 고객에게 전달하세요.

항상 한국어로 응답하세요."""

# AgentCore Runtime App 초기화
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context: RequestContext):
    """요청 처리 — Bearer Token을 추출해서 다른 Agent 호출 시 전달"""
    # 요청 헤더에서 Bearer Token 추출
    request_headers = context.request_headers or {}
    print(f"Headers: {list(request_headers.keys())}")

    # Authorization 헤더 (requestHeaderAllowlist로 전달됨)
    auth_header = request_headers.get("Authorization", "")
    bearer_token = auth_header.removeprefix("Bearer ").strip()

    # fallback: workloadaccesstoken
    if not bearer_token:
        bearer_token = request_headers.get("WorkloadAccessToken", "") or request_headers.get("workloadaccesstoken", "")

    if not bearer_token:
        return f"인증 토큰이 없습니다. 받은 헤더: {list(request_headers.keys())}"

    # A2A 클라이언트 — Bearer Token으로 다른 Agent 호출
    provider = A2AClientToolProvider(
        known_agent_urls=AGENT_URLS,
        httpx_client_args={
            "headers": {"Authorization": f"Bearer {bearer_token}"},
            "timeout": 300,
        },
    )

    # Agent 생성 (매 요청마다 — token이 다를 수 있으니)
    orchestrator = Agent(
        name="Orchestrator Agent",
        description="고객 질문을 분석해서 적절한 전문가 AI에게 라우팅하는 코디네이션 AI",
        model=model,
        tools=[provider.tools],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )

    user_input = payload.get("prompt", "")
    response = orchestrator(user_input)
    return response.message["content"][0]["text"]

if __name__ == "__main__":
    app.run()

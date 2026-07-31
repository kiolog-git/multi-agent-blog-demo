"""상담사 AI (Consultant Agent) — Gateway(MCP) → Lambda 연동"""

import os
import boto3
import requests
from strands import Agent
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import serve_a2a

# =============================================================================
# Gateway 인증 (Cognito → Bearer Token)
# =============================================================================

# 모델: Gateway(MCP)로 받은 도구를 호출하려면 tool use를 안정적으로 지원하는 모델이 필요합니다.
# Nova Pro는 MCP 도구명(예: tools-lambda___lookup_product) 호출 시
# modelStreamErrorException("Model produced invalid sequence as part of ToolUse")가 발생합니다.
# 리전: AgentCore Runtime이 주입하는 AWS_REGION을 사용합니다(setup.sh가 배포 리전을 전달).
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

ssm = boto3.client("ssm", region_name=REGION)
GATEWAY_URL = ssm.get_parameter(Name="/app/multiagent/blog/gateway_url")["Parameter"]["Value"]
COGNITO_CLIENT_ID = ssm.get_parameter(Name="/app/multiagent/blog/cognito_client_id")["Parameter"]["Value"]
COGNITO_TOKEN_URL = ssm.get_parameter(Name="/app/multiagent/blog/cognito_token_url")["Parameter"]["Value"]
COGNITO_SCOPE = ssm.get_parameter(Name="/app/multiagent/blog/cognito_scope")["Parameter"]["Value"]
COGNITO_SECRET = ssm.get_parameter(Name="/app/multiagent/blog/cognito_client_secret", WithDecryption=True)["Parameter"]["Value"]

# Cognito에서 machine token 발급
token_resp = requests.post(
    COGNITO_TOKEN_URL,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={
        "grant_type": "client_credentials",
        "client_id": COGNITO_CLIENT_ID,
        "client_secret": COGNITO_SECRET,
        "scope": COGNITO_SCOPE,
    },
)
BEARER_TOKEN = token_resp.json()["access_token"]

# =============================================================================
# MCP 연결 (AgentCore Gateway → Lambda → 도구)
# =============================================================================

mcp_client = MCPClient(
    lambda: streamablehttp_client(
        url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
    )
)

# =============================================================================
# Agent 생성 + A2A 서버
# =============================================================================

with mcp_client:
    agent = Agent(
        name="Consultant Agent",
        description="제품 스펙, 가격, 보증, 반품 정책을 안내하는 상담사 AI",
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=mcp_client.list_tools_sync(),
        system_prompt="당신은 상담사입니다. 도구를 사용해서 정확한 제품 정보를 제공하세요. 항상 한국어로 응답하세요.",
        callback_handler=None,
    )

    if __name__ == "__main__":
        serve_a2a(StrandsA2AExecutor(agent))

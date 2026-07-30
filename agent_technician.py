"""기술자 AI (Technician Agent) — Gateway(MCP) → Lambda 연동"""

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

ssm = boto3.client("ssm", region_name="us-east-1")
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
        name="Technician Agent",
        description="기기 과열, 배터리, 블루투스 등 기술 문제를 진단하고 해결책을 안내하는 기술자 AI",
        model=BedrockModel(model_id="us.amazon.nova-pro-v1:0", region_name="us-east-1"),
        tools=mcp_client.list_tools_sync(),
        system_prompt="당신은 기술자입니다. 도구를 사용해서 정확한 기술 정보를 제공하세요. 항상 한국어로 응답하세요.",
        callback_handler=None,
    )

    if __name__ == "__main__":
        serve_a2a(StrandsA2AExecutor(agent))

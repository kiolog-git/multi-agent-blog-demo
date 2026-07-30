"""
인프라 설정: AgentCore Gateway + Memory + Cognito
==================================================
블로그 다이어그램의 하단 인프라를 한 번에 세팅하는 스크립트.
한 번만 실행하면 되고, 이후 Agent들이 여기서 만든 리소스를 가져다 씁니다.

[블로그 매핑]
- AgentCore Gateway (MCP) → create_gateway()
- AgentCore Memory        → create_memory()
- Cognito (인증)          → setup_cognito()
- Lambda (실행 함수)      → Gateway에 Lambda target 연결
"""

import boto3
import time
import json
import os
import random
import string

# =============================================================================
# AWS 설정
# =============================================================================

session = boto3.Session()
REGION = session.region_name

ssm = boto3.client("ssm", region_name=REGION)
cognito = boto3.client("cognito-idp", region_name=REGION)
gateway_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

memory_client = MemoryClient(region_name=REGION)

# SSM 파라미터 경로 (모든 Agent가 공유)
PARAM_PREFIX = "/app/multiagent/blog"


def put_param(key, value):
    ssm.put_parameter(Name=f"{PARAM_PREFIX}/{key}", Value=value, Type="String", Overwrite=True)
    print(f"  저장: {PARAM_PREFIX}/{key}")


def get_param(key):
    return ssm.get_parameter(Name=f"{PARAM_PREFIX}/{key}")["Parameter"]["Value"]


# =============================================================================
# 1. Cognito 설정 (인증)
# =============================================================================

def setup_cognito():
    """Cognito User Pool + Machine Client 생성"""
    print("\n[1/3] Cognito 설정...")

    # User Pool 생성
    pool = cognito.create_user_pool(
        PoolName="MultiAgentBlogPool",
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = pool["UserPool"]["Id"]

    # 도메인 설정 (고유한 이름 생성)
    domain_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    domain_name = f"multiagent-blog-{domain_suffix}"
    cognito.create_user_pool_domain(
        Domain=domain_name,
        UserPoolId=pool_id,
    )

    # Resource Server (스코프 정의)
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier="gateway",
        Name="Gateway Access",
        Scopes=[{"ScopeName": "invoke", "ScopeDescription": "Invoke gateway tools"}],
    )

    # Machine Client (Agent가 사용할 인증 클라이언트)
    client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="AgentMachineClient",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=["gateway/invoke"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )
    client_id = client["UserPoolClient"]["ClientId"]
    client_secret = client["UserPoolClient"]["ClientSecret"]

    # 토큰 URL 구성
    token_url = f"https://{domain_name}.auth.{REGION}.amazoncognito.com/oauth2/token"
    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

    # 파라미터 저장
    put_param("cognito_pool_id", pool_id)
    put_param("cognito_client_id", client_id)
    put_param("cognito_token_url", token_url)
    put_param("cognito_discovery_url", discovery_url)
    put_param("cognito_scope", "gateway/invoke")
    ssm.put_parameter(
        Name=f"{PARAM_PREFIX}/cognito_client_secret",
        Value=client_secret, Type="SecureString", Overwrite=True
    )
    print(f"  저장: {PARAM_PREFIX}/cognito_client_secret (암호화)")

    print(f"  Cognito User Pool: {pool_id}")
    print(f"  Client ID: {client_id}")

    # UI용 Client (사용자 로그인용 — USER_PASSWORD_AUTH 지원)
    ui_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="WebUIClient",
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        GenerateSecret=False,
        SupportedIdentityProviders=["COGNITO"],
    )
    ui_client_id = ui_client["UserPoolClient"]["ClientId"]
    put_param("cognito_ui_client_id", ui_client_id)
    print(f"  UI Client ID: {ui_client_id}")

    # 테스트 사용자 생성 (demo / Demo1234!)
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username="demo",
        TemporaryPassword="Demo1234!",
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=pool_id,
        Username="demo",
        Password="Demo1234!",
        Permanent=True,
    )
    print(f"  테스트 사용자 생성: demo / Demo1234!")

    return pool_id, client_id


# =============================================================================
# 2. AgentCore Memory 설정 (공유 기억)
# =============================================================================

def setup_memory():
    """AgentCore Memory 리소스 생성 — 모든 Agent가 공유"""
    print("\n[2/3] AgentCore Memory 설정...")

    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "SharedConversationMemory",
                "description": "모든 Agent가 공유하는 대화 기억",
                "namespaces": ["shared/customer/{actorId}/conversations"],
            }
        },
        {
            StrategyType.USER_PREFERENCE.value: {
                "name": "CustomerPreferences",
                "description": "고객 선호도 저장",
                "namespaces": ["shared/customer/{actorId}/preferences"],
            }
        },
    ]

    print("  Memory 리소스 생성 중 (2-3분 소요)...")

    # Memory 생성 (SDK가 ACTIVE될 때까지 내부에서 대기)
    create_response = memory_client.gmcp_client.create_memory(
        name="BlogDemoMemory",
        description="Multi-agent shared memory for blog demo",
        memoryStrategies=strategies,
        eventExpiryDuration=90,
    )
    memory_id = create_response["memory"]["id"]

    # 상태 폴링
    while True:
        get_response = memory_client.gmcp_client.get_memory(memoryId=memory_id)
        status = get_response["memory"]["status"]
        print(f"    상태: {status}")
        if status.upper() in ("ACTIVE", "READY"):
            break
        time.sleep(10)

    put_param("memory_id", memory_id)
    print(f"  Memory ID: {memory_id}")
    return memory_id


# =============================================================================
# 3. AgentCore Gateway 설정 (MCP + Lambda + Cognito 인증)
# =============================================================================

def setup_gateway(lambda_arn: str):
    """AgentCore Gateway 생성 — MCP 프로토콜, Cognito 인증, Lambda 연결"""
    print("\n[3/3] AgentCore Gateway 설정...")

    client_id = get_param("cognito_client_id")
    discovery_url = get_param("cognito_discovery_url")

    # Gateway 생성 (MCP + Cognito JWT 인증)
    role_arn = os.environ.get("GATEWAY_ROLE_ARN", "")
    response = gateway_client.create_gateway(
        name="blog-demo-gateway",
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "allowedClients": [client_id],
                "discoveryUrl": discovery_url,
            }
        },
        description="Blog demo: MCP gateway with Cognito auth",
    )
    gateway_id = response["gatewayId"]
    gateway_url = response["gatewayUrl"]

    # Gateway가 활성화될 때까지 대기
    print("  Gateway 활성화 대기...")
    while True:
        status = gateway_client.get_gateway(gatewayIdentifier=gateway_id)["status"]
        print(f"    상태: {status}")
        if status.upper() in ("ACTIVE", "READY"):
            break
        time.sleep(5)

    # Lambda Target 추가 (도구 실행 함수 + 도구 스펙)
    api_spec_path = os.path.join(os.path.dirname(__file__), "lambda", "api_spec.json")
    with open(api_spec_path) as f:
        tool_schema = json.load(f)

    gateway_client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="tools-lambda",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema},
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )

    put_param("gateway_id", gateway_id)
    put_param("gateway_url", gateway_url)

    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    print(f"  Lambda 연결: {lambda_arn}")
    return gateway_id, gateway_url


# =============================================================================
# 실행
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Multi-Agent 인프라 설정")
    print("  (Gateway + Memory + Cognito)")
    print("=" * 60)

    # 1. Cognito
    setup_cognito()

    # 2. Memory
    setup_memory()

    # 3. Gateway
    LAMBDA_ARN = os.environ.get("LAMBDA_ARN") or input("\n  Lambda ARN 입력: ").strip()
    setup_gateway(LAMBDA_ARN)

    print("\n" + "=" * 60)
    print("  인프라 설정 완료!")
    print("  이제 Agent 프로그램들을 실행할 수 있습니다.")
    print("=" * 60)

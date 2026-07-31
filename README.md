# Multi-Agent Blog Demo

AWS Bedrock AgentCore로 구현한 멀티에이전트 고객지원 시스템.

블로그 시리즈 [kiolog](https://blog.naver.com/kiolog) 다이어그램과 1:1로 매핑되는 실행 가능한 코드입니다.

## 구조

```
├── agent_orchestrator.py    ← 코디네이션 AI (전문가 AI를 A2A로 호출)
├── agent_consultant.py      ← 상담사 AI (Gateway→Lambda로 제품 조회)
├── agent_technician.py      ← 기술자 AI (Gateway→Lambda로 기술 진단)
├── web_ui.py                ← 채팅 UI (Streamlit + Cognito 로그인)
├── bedrock_infra_build.py   ← 인프라 (Cognito + Memory + Gateway)
├── setup.sh                 ← 원클릭 설정 스크립트
├── Dockerfile               ← Agent 컨테이너 빌드 (ARM64)
├── requirements-agent.txt   ← Agent 컨테이너 의존성
├── requirements.txt         ← 로컬 개발 의존성
└── lambda/                  ← MCP 도구 (Gateway 연동용)
```

## Agent 코드 핵심 패턴

### 모델 선택

세 Agent 모두 `us.anthropic.claude-haiku-4-5-20251001-v1:0`을 씁니다.

Gateway(MCP)가 넘겨주는 도구 이름은 `tools-lambda___lookup_product` 형태입니다. 이 도구를 호출할 때
`us.amazon.nova-pro-v1:0`은 다음 오류로 실패합니다:

```
modelStreamErrorException: Model produced invalid sequence as part of ToolUse
```

Agent 작업이 `failed` 상태로 끝나므로, MCP 도구를 쓰려면 tool use가 안정적인 모델이 필요합니다.
`setup.sh`가 배포 전에 이 모델 호출 가능 여부를 먼저 확인합니다.

### 전문가 AI (consultant, technician) — A2A 프로토콜 + Gateway(MCP) 도구

도구를 Agent 안에 두지 않고, AgentCore Gateway를 통해 Lambda에서 실행합니다:

```python
from strands import Agent
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import serve_a2a

# Cognito client_credentials로 machine token 발급 → Gateway 인증에 사용
mcp_client = MCPClient(lambda: streamablehttp_client(
    url=GATEWAY_URL,
    headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
))

with mcp_client:
    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=mcp_client.list_tools_sync(),   # ← Gateway가 알려주는 Lambda 도구
        system_prompt="...",
    )

    if __name__ == "__main__":
        serve_a2a(StrandsA2AExecutor(agent))
```

`serve_a2a`가 /ping, agent card, 0.0.0.0:9000 바인딩을 전부 자동 처리합니다.
도구 실행은 Agent 컨테이너가 아니라 **Lambda**에서 일어납니다.

### 코디네이션 AI (orchestrator) — HTTP 프로토콜 + A2A 클라이언트

전문가 AI를 A2A JSON-RPC(`message/send`)로 직접 호출합니다:

```python
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

def call_a2a_agent(agent_url: str, message: str) -> str:
    resp = requests.post(agent_url, json={
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
        "params": {"message": {"messageId": str(uuid.uuid4()), "role": "user",
                               "parts": [{"kind": "text", "text": message}]}},
    }, headers={"Authorization": f"Bearer {_current_token['value']}"}, timeout=120)
    result = resp.json()["result"]

    # HTTP 200이어도 task가 failed로 끝날 수 있습니다.
    # 이때 history에는 중간에 끊긴 부분 텍스트만 남으므로 성공으로 오인하면 안 됩니다.
    state = result.get("status", {}).get("state")
    if state and state != "completed":
        raise RuntimeError(f"전문가 AI 작업이 '{state}' 상태로 종료되었습니다.")
    ...

@tool(name="ask_consultant", description="상담사 AI에게 질문합니다. ...")
def ask_consultant(question: str) -> str:
    # URL을 요청 시점에 SSM에서 읽습니다.
    # 모듈 로드 시 한 번만 읽으면, 전문가 AI 재배포로 Runtime ID가 바뀔 때
    # 이미 떠 있는 컨테이너가 옛 URL을 계속 호출해 404가 납니다.
    return call_a2a_agent(get_agent_url("agent_consultant"), question)

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context: RequestContext):
    # requestHeaderAllowlist로 전달된 호출자 토큰을 그대로 전문가에게 전달
    auth = context.request_headers.get("authorization", "")
    _current_token["value"] = auth[7:] if auth.startswith("Bearer ") else auth

    orchestrator = Agent(model=model, tools=[ask_consultant, ask_technician], ...)
    return orchestrator(payload["prompt"]).message["content"][0]["text"]
```

`BedrockAgentCoreApp`은 HTTP 프로토콜(포트 8080)로 요청을 받습니다.

## 사전 요구사항

- AWS CLI 설정 완료 (`aws configure`)
- Python 3.11+
- docker 또는 [finch](https://github.com/runfinch/finch)
- AWS 계정 권한: Cognito, Lambda, Bedrock AgentCore, SSM, ECR, IAM
- Bedrock에서 **Anthropic Claude 모델 액세스 활성화**
  ([Model access 콘솔](https://console.aws.amazon.com/bedrock/home#/modelaccess))

## 실행 방법

```bash
# 1. 클론
git clone https://github.com/kiolog-git/multi-agent-blog-demo
cd multi-agent-blog-demo

# 2. 가상환경
python3 -m venv venv && source venv/bin/activate

# 3. 원클릭 설정 (인프라 + 컨테이너 + Runtime 배포)
./setup.sh

# 4. UI 실행
streamlit run web_ui.py

# 5. 로그인
#    아이디: demo
#    비밀번호: Demo1234!
```

## setup.sh가 하는 일

| Step | 내용 | 소요 시간 |
|---|---|---|
| 0 | Bedrock 모델 액세스 확인 | ~2초 |
| 0 | 기존 리소스 정리 (멱등성 보장) | ~30초 |
| 1 | Python 의존성 설치 | ~10초 |
| 2 | IAM Role 생성 (Lambda + Gateway + AgentCore) | ~25초 |
| 3 | Lambda 배포 | ~5초 |
| 4 | Cognito + Memory + Gateway 구성 | ~3분 |
| 5 | Agent 컨테이너 빌드(ARM64) + Runtime 배포(protocol=A2A) | ~2분 |

총 소요 시간: 약 **5~7분**

## 배포 시 주요 설정

| 항목 | 값 | 이유 |
|---|---|---|
| `--platform linux/arm64` | ARM64 빌드 | AgentCore Runtime 요구사항 |
| `protocolConfiguration: A2A` | consultant, technician | 포트 9000, `/` 경로 사용 |
| `protocolConfiguration: HTTP` | orchestrator | 포트 8080, 호출자 토큰 수신 필요 |
| `requestHeaderAllowlist: ["Authorization"]` | orchestrator | 호출자 토큰을 컨테이너로 전달 |
| `environmentVariables: AWS_REGION` | 3개 전부 | 코드가 리전을 하드코딩하지 않도록 |
| `.pyc` 프리컴파일 | cold start 단축 | 120초 초기화 제한 |
| `serve_a2a()` | SDK 자동 처리 | /ping, agent card 자동 |

`requestHeaderAllowlist`는 Runtime을 **생성할 때만** 지정할 수 있습니다. 나중에 추가하려면 재생성이 필요합니다.

## 전체 흐름 (고객이 질문했을 때)

```
1. web_ui.py → Cognito 로그인 → Bearer Token으로 orchestrator 호출
2. orchestrator → requestHeaderAllowlist로 받은 토큰을 그대로 실어
                  A2A message/send로 consultant 호출
3. consultant → Gateway(MCP)로 도구 호출 → Gateway가 Lambda 실행 → 제품 정보 반환
4. orchestrator → 결과 종합 → web_ui에 응답 → 고객에게 표시
```

동작을 직접 확인하려면:

```bash
# Lambda가 실제로 실행됐는지
aws logs filter-log-events --log-group-name /aws/lambda/blog-demo-tools \
  --filter-pattern "Tool:" --region us-east-1

# orchestrator가 A2A 호출을 완료했는지 (state=completed 확인)
aws logs filter-log-events \
  --log-group-name /aws/bedrock-agentcore/runtimes/<orchestrator-id>-DEFAULT \
  --filter-pattern "task state" --region us-east-1
```

## 정리

- **A2A + serve_a2a**: Agent를 A2A 서버로 배포하는 한 줄
- **Gateway(MCP)**: 도구를 Agent 밖(Lambda)에서 실행하고 목록만 받아오는 구조
- **Strands SDK**: Agent 생성 + A2A 서버를 몇 줄로 가능하게 해주는 도구
- **AgentCore Runtime**: 컨테이너를 관리형으로 실행하는 AWS 서비스
- **Cognito**: 사용자 로그인 + Gateway 접근 인증

# Multi-Agent Blog Demo

AWS Bedrock AgentCore로 구현한 멀티에이전트 고객지원 시스템.

블로그 시리즈 [kiolog](https://blog.naver.com/kiolog) 다이어그램과 1:1로 매핑되는 실행 가능한 코드입니다.

## 구조

```
├── agent_orchestrator.py    ← 코디네이션 AI
├── agent_consultant.py      ← 상담사 AI (제품 조회 도구 포함)
├── agent_technician.py      ← 기술자 AI (기술 진단 도구 포함)
├── web_ui.py                ← 채팅 UI (Streamlit + Cognito 로그인)
├── bedrock_infra_build.py   ← 인프라 (Cognito + Memory + Gateway)
├── setup.sh                 ← 원클릭 설정 스크립트
├── Dockerfile               ← Agent 컨테이너 빌드 (ARM64)
├── requirements-agent.txt   ← Agent 컨테이너 의존성
├── requirements.txt         ← 로컬 개발 의존성
└── lambda/                  ← MCP 도구 (Gateway 연동용)
```

## Agent 코드 핵심 패턴

### 전문가 AI (consultant, technician) — A2A 프로토콜

AWS 문서 권장 방식 그대로:

```python
from strands import Agent, tool
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from strands.models import BedrockModel
from bedrock_agentcore.runtime import serve_a2a

@tool(name="my_tool", description="도구 설명")
def my_tool(query: str) -> str:
    return "결과"

agent = Agent(
    model=BedrockModel(model_id="us.amazon.nova-pro-v1:0"),
    tools=[my_tool],
    system_prompt="...",
)

if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent))
```

`serve_a2a`가 /ping, agent card, 0.0.0.0:9000 바인딩을 전부 자동 처리합니다.

### 코디네이션 AI (orchestrator) — HTTP 프로토콜 + A2A 클라이언트

다른 Agent를 호출해야 하므로 구조가 다릅니다:

```python
from strands import Agent
from strands_tools.a2a_client import A2AClientToolProvider
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context: RequestContext):
    # 인증 토큰을 전문가 AI에 그대로 전달
    bearer_token = context.request_headers.get("Authorization", "")
    provider = A2AClientToolProvider(
        known_agent_urls=AGENT_URLS,
        httpx_client_args={"headers": {"Authorization": bearer_token}},
    )
    orchestrator = Agent(model=..., tools=[provider.tools], ...)
    return orchestrator(payload["prompt"])

if __name__ == "__main__":
    app.run()
```

`BedrockAgentCoreApp`은 HTTP 프로토콜(포트 8080)로 요청을 받고, `A2AClientToolProvider`로 전문가 AI를 A2A 호출합니다.

## 사전 요구사항

- AWS CLI 설정 완료 (`aws configure`)
- Python 3.11+
- docker 또는 [finch](https://github.com/runfinch/finch)
- AWS 계정 권한: Cognito, Lambda, Bedrock AgentCore, SSM, ECR, IAM

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
| `protocolConfiguration: A2A` | A2A 프로토콜 | 포트 9000, / 경로 사용 |
| `.pyc` 프리컴파일 | cold start 단축 | 120초 초기화 제한 |
| `serve_a2a()` | SDK 자동 처리 | /ping, agent card 자동 |

## 전체 흐름 (고객이 질문했을 때)

```
1. web_ui.py → Cognito 로그인 → Bearer Token으로 orchestrator 호출
2. orchestrator → 토큰을 전문가에게 전달 → A2AClientToolProvider로 consultant 호출
3. consultant → @tool(lookup_product) 실행 → 제품 정보 반환
4. orchestrator → 결과 종합 → web_ui에 응답 → 고객에게 표시
```

## 정리

- **A2A + serve_a2a**: Agent를 A2A 서버로 배포하는 한 줄
- **@tool**: Agent가 사용하는 도구를 Python 함수로 정의
- **Strands SDK**: Agent 생성 + A2A 서버를 몇 줄로 가능하게 해주는 도구
- **AgentCore Runtime**: 컨테이너를 관리형으로 실행하는 AWS 서비스
- **Cognito**: 사용자 로그인 + Gateway 접근 인증

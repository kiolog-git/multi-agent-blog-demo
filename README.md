# Multi-Agent Blog Demo

AWS Bedrock AgentCore로 구현한 멀티에이전트 고객지원 시스템.

블로그 시리즈 [kiolog](https://blog.naver.com/kiolog) 3편 다이어그램과 1:1로 매핑되는 실행 가능한 코드입니다.

## 파일 구조

```
├── agent_orchestrator.py    ← 코디네이션 AI (질문을 분석해서 전문가에게 라우팅)
├── agent_consultant.py      ← 상담사 AI (Gateway→Lambda로 제품 조회)
├── agent_technician.py      ← 기술자 AI (Gateway→Lambda로 기술 진단)
├── web_ui.py                ← 채팅 UI (Streamlit + Cognito 로그인)
├── bedrock_infra_build.py   ← 인프라 (Cognito + Memory + Gateway)
├── setup.sh                 ← 원클릭 설정 스크립트
├── Dockerfile               ← Agent 컨테이너 빌드 (ARM64)
├── requirements-agent.txt   ← Agent 컨테이너 의존성
├── requirements.txt         ← 로컬 개발 의존성
└── lambda/                  ← Lambda 도구 (lookup_product, diagnose_issue)
```

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

# 3. 원클릭 설정 (인프라 + 컨테이너 + Runtime 배포, 약 5~7분)
./setup.sh

# 4. UI 실행
streamlit run web_ui.py

# 5. 로그인: demo / Demo1234!
```

## 전체 흐름

고객이 "Galaxy S25 Ultra 반품 정책 알려줘"라고 물었을 때:

```
1. web_ui → Cognito 로그인 → Bearer Token으로 orchestrator 호출
2. orchestrator → 질문 분석 → "제품 관련이니까 상담사에게 보내자"
3. orchestrator → 받은 토큰을 그대로 실어 A2A로 consultant 호출
4. consultant → Gateway(MCP)에 도구 호출 요청
5. Gateway → Lambda 실행 (lookup_product)
6. Lambda → "미개봉 30일, 개봉 14일 이내 반품 가능" 반환
7. consultant → orchestrator → web_ui → 고객에게 표시
```

## 핵심 개념

### 왜 orchestrator만 HTTP 프로토콜인가?

| Agent | 프로토콜 | 이유 |
|---|---|---|
| consultant, technician | A2A (포트 9000) | 다른 Agent로부터 호출받기만 함 |
| orchestrator | HTTP (포트 8080) | web_ui로부터 직접 호출 + 호출자 토큰을 받아야 함 |

### requestHeaderAllowlist

AgentCore Runtime은 보안상 **web_ui가 보낸 헤더를 컨테이너에 전달하지 않습니다.**

그런데 orchestrator는 web_ui가 보낸 인증 토큰을 받아서 전문가 AI에게 다시 보내야 해요.
`requestHeaderAllowlist: ["Authorization"]`을 설정하면 AgentCore Runtime이 Authorization 헤더를 벗기지 않고 컨테이너까지 전달합니다.

```
web_ui → [Authorization: Bearer xxx] → AgentCore Runtime
                                              ↓ allowlist에 있으므로 통과
                             orchestrator 컨테이너가 토큰을 받음
                                              ↓
                  orchestrator가 consultant 호출 시 같은 토큰을 헤더에 넣어서 보냄
```

이 설정이 없으면 orchestrator는 토큰을 받지 못하고, 전문가 AI 호출 시 403이 납니다.

**주의**: Runtime을 **만들 때만** 지정 가능. 나중에 추가하려면 Runtime 삭제 후 재생성 필요.

### 모델 선택

세 Agent 모두 `us.anthropic.claude-haiku-4-5-20251001-v1:0`을 사용합니다.

`us.amazon.nova-pro-v1:0`은 Gateway가 넘겨주는 도구(`tools-lambda___lookup_product`)를 호출하려는 순간 잘못된 형식의 응답을 보내서 에러가 납니다:

```
modelStreamErrorException: Model produced invalid sequence as part of ToolUse
```

MCP 도구를 쓸 때는 tool use가 안정적인 모델을 선택하세요.
`setup.sh`가 배포 전에 모델 호출 가능 여부를 먼저 확인합니다.

### A2A 응답의 state 확인

A2A 호출은 **HTTP 200을 받아도 실패한 것일 수 있습니다.** 응답의 `status.state`를 반드시 확인하세요:

| state | 의미 |
|---|---|
| `completed` | 정상 완료 |
| `failed` | 모델 에러 등으로 실패 (history에 잘린 텍스트만 남음) |
| `working` | 아직 처리 중 |

`completed`가 아니면 실패로 처리해야 합니다.

## setup.sh가 하는 일

| Step | 내용 | 소요 시간 |
|---|---|---|
| 0 | 모델 액세스 확인 + 기존 리소스 정리 | ~30초 |
| 1 | Python 의존성 설치 | ~10초 |
| 2 | IAM Role 생성 | ~25초 |
| 3 | Lambda 배포 | ~5초 |
| 4 | Cognito + Memory + Gateway 구성 | ~3분 |
| 5 | Agent 컨테이너 빌드(ARM64) + Runtime 배포 | ~2분 |

정리가 끝나면 "새로 생성 / 중지" 메뉴가 나옵니다.
정리만 하고 싶으면 2를 선택하세요.

## 배포 설정 요약

| 항목 | 값 | 이유 |
|---|---|---|
| `--platform linux/arm64` | ARM64 빌드 | AgentCore Runtime 요구사항 |
| `protocolConfiguration: A2A` | consultant, technician | serve_a2a() 사용, 포트 9000 |
| `protocolConfiguration: HTTP` | orchestrator | BedrockAgentCoreApp, 포트 8080 |
| `requestHeaderAllowlist` | orchestrator만 | 호출자 토큰을 컨테이너로 전달 |
| `environmentVariables: AWS_REGION` | 3개 전부 | 리전 하드코딩 방지 |
| `.pyc` 프리컴파일 | Dockerfile | 120초 cold start 제한 대응 |

## 동작 확인 (로그)

```bash
# Lambda가 실제로 실행됐는지
aws logs filter-log-events --log-group-name /aws/lambda/blog-demo-tools \
  --filter-pattern "Tool:" --region us-east-1

# orchestrator가 A2A 호출을 완료했는지
aws logs filter-log-events \
  --log-group-name /aws/bedrock-agentcore/runtimes/<orchestrator-id>-DEFAULT \
  --filter-pattern "task state" --region us-east-1
```

## 정리

- **serve_a2a()**: Agent를 A2A 서버로 배포하는 한 줄
- **Gateway(MCP)**: Lambda 도구를 Agent에 연결하는 중간 계층
- **requestHeaderAllowlist**: 호출자 토큰을 컨테이너로 넘기는 허용 목록
- **AgentCore Runtime**: 컨테이너를 관리형으로 실행하는 AWS 서비스
- **Cognito**: 사용자 로그인 + Agent 간 인증

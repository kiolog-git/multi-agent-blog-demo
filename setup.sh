#!/bin/bash
# =============================================================================
# Multi-Agent Blog Demo — 원클릭 설정 스크립트
# =============================================================================
# 사전 요구사항:
# - AWS CLI 설정 완료 (aws configure)
# - Python 3.11+
# - docker 또는 finch
# - IAM 권한 (Cognito, Lambda, Bedrock AgentCore, SSM, ECR)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION=$(aws configure get region || echo "us-west-2")
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 리소스 이름 (상수)
LAMBDA_NAME="blog-demo-tools"
LAMBDA_ROLE="blog-demo-lambda-role"
GW_ROLE="blog-demo-gateway-role"
ECR_REPO="blog-demo-agents"
COGNITO_POOL_NAME="MultiAgentBlogPool"
MEMORY_PREFIX="BlogDemoMemory"
SSM_PREFIX="/app/multiagent/blog"

# 컨테이너 런타임 감지 + VM 시작
if command -v docker >/dev/null 2>&1; then
    CONTAINER_CMD="docker"
elif command -v finch >/dev/null 2>&1; then
    CONTAINER_CMD="finch"
    # Finch VM이 꺼져있으면 시작
    if ! finch vm status 2>/dev/null | grep -q "Running"; then
        echo "  Finch VM 시작 중..."
        finch vm start >/dev/null 2>&1
    fi
else
    echo "❌ docker 또는 finch가 필요합니다."
    exit 1
fi

# =============================================================================
# 사전 점검: Bedrock 모델 액세스
# =============================================================================
# 에이전트가 사용하는 모델. agent_*.py의 MODEL_ID와 반드시 동일해야 합니다.
# Gateway(MCP)로 받은 도구를 호출하려면 tool use가 안정적인 모델이 필요합니다.
# Nova Pro는 MCP 도구명(tools-lambda___lookup_product) 호출 시
# modelStreamErrorException("Model produced invalid sequence as part of ToolUse")로 실패합니다.
MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"

echo "  Bedrock 모델 액세스 확인: $MODEL_ID"
if ! aws bedrock-runtime converse \
    --model-id "$MODEL_ID" \
    --messages '[{"role":"user","content":[{"text":"hi"}]}]' \
    --inference-config '{"maxTokens":1}' \
    --region "$REGION" >/dev/null 2>&1; then
    echo ""
    echo "❌ 모델을 호출할 수 없습니다: $MODEL_ID"
    echo "   Bedrock 콘솔 → Model access 에서 Anthropic Claude 모델 액세스를 활성화하세요."
    echo "   https://console.aws.amazon.com/bedrock/home?region=${REGION}#/modelaccess"
    exit 1
fi
echo "    OK"

# =============================================================================
# 헬퍼 함수
# =============================================================================

wait_memory_deleted() {
    local mem_id=$1
    while aws bedrock-agentcore-control list-memories --region "$REGION" \
        --query "memories[?id=='$mem_id'].id" --output text 2>/dev/null | grep -q "$mem_id"; do
        sleep 5
    done
}

delete_gateway() {
    local gw_id=$1
    # Target 먼저 삭제
    for TID in $(aws bedrock-agentcore-control list-gateway-targets --gateway-id "$gw_id" --region "$REGION" \
        --query 'items[].targetId' --output text 2>/dev/null); do
        aws bedrock-agentcore-control delete-gateway-target --gateway-id "$gw_id" --target-id "$TID" --region "$REGION" 2>/dev/null || true
    done
    sleep 5
    aws bedrock-agentcore-control delete-gateway --gateway-id "$gw_id" --region "$REGION" 2>/dev/null || true
}

delete_cognito_pool() {
    local pool_id=$1
    local domain=$(aws cognito-idp describe-user-pool --user-pool-id "$pool_id" \
        --query 'UserPool.Domain' --output text 2>/dev/null || echo "None")
    if [ "$domain" != "None" ] && [ -n "$domain" ]; then
        aws cognito-idp delete-user-pool-domain --user-pool-id "$pool_id" --domain "$domain" 2>/dev/null || true
    fi
    aws cognito-idp delete-user-pool --user-pool-id "$pool_id" 2>/dev/null || true
}

delete_iam_role() {
    local role=$1
    if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
        echo "  IAM Role ($role) → 삭제..."
        for pol in $(aws iam list-attached-role-policies --role-name "$role" \
            --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
            aws iam detach-role-policy --role-name "$role" --policy-arn "$pol" 2>/dev/null || true
        done
        for pol in $(aws iam list-role-policies --role-name "$role" \
            --query 'PolicyNames[]' --output text 2>/dev/null); do
            aws iam delete-role-policy --role-name "$role" --policy-name "$pol" 2>/dev/null || true
        done
        aws iam delete-role --role-name "$role" 2>/dev/null || true
        # 삭제 확인 대기
        while aws iam get-role --role-name "$role" >/dev/null 2>&1; do
            sleep 5
        done
        echo "  IAM Role ($role) 삭제 완료"
        return 0
    fi
    echo "  IAM Role ($role): 없음"
    return 1
}

# =============================================================================
echo ""
echo "============================================================"
echo "  Multi-Agent Blog Demo 설정"
echo "  리전: $REGION | 계정: $ACCOUNT_ID"
echo "============================================================"

# =============================================================================
# [Step 0] 기존 리소스 정리
# =============================================================================
echo ""
echo "[Step 0/6] 기존 리소스 정리..."

# Gateway (SSM 또는 API 검색)
GW_ID=$(aws ssm get-parameter --name "$SSM_PREFIX/gateway_id" --query 'Parameter.Value' --output text 2>/dev/null || \
    aws bedrock-agentcore-control list-gateways --region "$REGION" --query "items[?starts_with(name,'blog-demo')].gatewayId | [0]" --output text 2>/dev/null || echo "")
if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
    echo "  Gateway ($GW_ID) → 삭제..."
    delete_gateway "$GW_ID"
    echo "  Gateway 삭제 완료"
else
    echo "  Gateway: 없음"
fi

# Memory (SSM 또는 API 검색)
MEM_ID=$(aws ssm get-parameter --name "$SSM_PREFIX/memory_id" --query 'Parameter.Value' --output text 2>/dev/null || \
    aws bedrock-agentcore-control list-memories --region "$REGION" --query "memories[?starts_with(id,'$MEMORY_PREFIX')].id | [0]" --output text 2>/dev/null || echo "")
if [ -n "$MEM_ID" ] && [ "$MEM_ID" != "None" ]; then
    echo "  Memory ($MEM_ID) → 삭제..."
    aws bedrock-agentcore-control delete-memory --memory-id "$MEM_ID" --region "$REGION" 2>/dev/null || true
    wait_memory_deleted "$MEM_ID"
    echo "  삭제 완료"
else
    echo "  Memory: 없음"
fi

# Cognito (SSM 또는 API 검색)
POOL_ID=$(aws ssm get-parameter --name "$SSM_PREFIX/cognito_pool_id" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
COGNITO_FOUND=false
if [ -n "$POOL_ID" ] && [ "$POOL_ID" != "None" ]; then
    echo "  Cognito ($POOL_ID) → 삭제..."
    delete_cognito_pool "$POOL_ID"
    echo "  Cognito 삭제 완료"
    COGNITO_FOUND=true
fi
# 잔여 Pool도 검색해서 삭제
for PID in $(aws cognito-idp list-user-pools --max-results 20 \
    --query "UserPools[?Name=='$COGNITO_POOL_NAME'].Id" --output text 2>/dev/null); do
    [ -n "$PID" ] && echo "  Cognito 잔여 ($PID) → 삭제..." && delete_cognito_pool "$PID" && COGNITO_FOUND=true
done
if [ "$COGNITO_FOUND" = false ]; then
    echo "  Cognito: 없음"
fi

# Lambda
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  Lambda ($LAMBDA_NAME) → 삭제..."
    aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null
    echo "  Lambda 삭제 완료"
else
    echo "  Lambda: 없음"
fi

# ECR
if aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1; then
    echo "  ECR ($ECR_REPO) → 삭제..."
    aws ecr delete-repository --repository-name "$ECR_REPO" --region "$REGION" --force >/dev/null
    echo "  ECR 삭제 완료"
else
    echo "  ECR: 없음"
fi

# AgentCore Runtime (모든 blog 관련 Runtime 삭제)
RUNTIME_DELETED=false
for RUNTIME_ID in $(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query "agentRuntimes[].agentRuntimeId" --output text 2>/dev/null); do
    if [ -n "$RUNTIME_ID" ] && [ "$RUNTIME_ID" != "None" ]; then
        echo "  Runtime ($RUNTIME_ID) → 삭제..."
        aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" 2>/dev/null || true
        RUNTIME_DELETED=true
    fi
done
if [ "$RUNTIME_DELETED" = true ]; then
    echo "  Runtime 삭제 대기..."
    while true; do
        COUNT=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
            --query "length(agentRuntimes)" --output text 2>/dev/null || echo "0")
        if [ "$COUNT" = "0" ] || [ -z "$COUNT" ]; then
            break
        fi
        sleep 10
    done
    echo "  Runtime 삭제 완료"
else
    echo "  Runtime: 없음"
fi

# SSM 파라미터
SSM_PARAMS=$(aws ssm get-parameters-by-path --path "$SSM_PREFIX" --recursive \
    --query 'Parameters[].Name' --output text --region "$REGION" 2>/dev/null || echo "")
if [ -n "$SSM_PARAMS" ]; then
    echo "  SSM 파라미터 → 삭제..."
    for param in $SSM_PARAMS; do
        aws ssm delete-parameter --name "$param" --region "$REGION" >/dev/null 2>&1 || true
    done
    echo "  SSM 삭제 완료"
else
    echo "  SSM: 없음"
fi

# IAM Role
IAM_DELETED=false
delete_iam_role "$LAMBDA_ROLE" && IAM_DELETED=true
delete_iam_role "$GW_ROLE" && IAM_DELETED=true
if [ "$IAM_DELETED" = true ]; then
    echo "  IAM 전파 대기 (30초)..."
    sleep 30
fi

echo "  ✅ 정리 완료"

# =============================================================================
# [Step 1] Python 의존성
# =============================================================================
echo ""
echo "[Step 1/6] Python 의존성 설치..."
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "  완료"

# =============================================================================
# [Step 2] IAM Role 생성
# =============================================================================
echo ""
echo "[Step 2/6] IAM Role 생성..."

aws iam create-role --role-name "$LAMBDA_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --query 'Role.Arn' --output text >/dev/null
aws iam attach-role-policy --role-name "$LAMBDA_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
echo "  $LAMBDA_ROLE 생성 완료"

aws iam create-role --role-name "$GW_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --query 'Role.Arn' --output text >/dev/null
aws iam attach-role-policy --role-name "$GW_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/AWSLambda_FullAccess"
aws iam attach-role-policy --role-name "$GW_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
aws iam attach-role-policy --role-name "$GW_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess"
aws iam attach-role-policy --role-name "$GW_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"

# AgentCore Runtime 실행에 필요한 추가 권한
aws iam put-role-policy --role-name "$GW_ROLE" --policy-name "AgentCoreRuntimePolicy" --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"bedrock-agentcore:GetWorkloadAccessToken\", \"bedrock-agentcore:GetWorkloadAccessTokenForJWT\", \"bedrock-agentcore:GetWorkloadAccessTokenForUserId\"],
      \"Resource\": \"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:workload-identity-directory/default*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"logs:CreateLogGroup\", \"logs:CreateLogStream\", \"logs:PutLogEvents\", \"logs:DescribeLogStreams\"],
      \"Resource\": \"arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": \"ecr:GetAuthorizationToken\",
      \"Resource\": \"*\"
    }
  ]
}"
echo "  $GW_ROLE 생성 완료"

echo "  Role 전파 대기 (10초)..."
sleep 10

# =============================================================================
# [Step 3] Lambda 배포
# =============================================================================
echo ""
echo "[Step 3/6] Lambda 배포..."

cd "$SCRIPT_DIR/lambda" && zip -q -r /tmp/blog-demo-lambda.zip . && cd "$SCRIPT_DIR"

LAMBDA_ARN=$(aws lambda create-function \
    --function-name "$LAMBDA_NAME" \
    --runtime python3.11 \
    --handler lambda_function.lambda_handler \
    --role "arn:aws:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE}" \
    --zip-file fileb:///tmp/blog-demo-lambda.zip \
    --timeout 30 \
    --region "$REGION" \
    --query 'FunctionArn' --output text)
echo "  생성 완료: $LAMBDA_ARN"

aws lambda add-permission \
    --function-name "$LAMBDA_NAME" \
    --statement-id "AllowAgentCoreGateway" \
    --action "lambda:InvokeFunction" \
    --principal "bedrock-agentcore.amazonaws.com" \
    --region "$REGION" >/dev/null 2>&1 || true

rm -f /tmp/blog-demo-lambda.zip

# =============================================================================
# [Step 4] Bedrock 인프라 (Cognito + Memory + Gateway)
# =============================================================================
echo ""
echo "[Step 4/6] Bedrock 인프라 구성 (Cognito + Memory + Gateway)..."

export LAMBDA_ARN
export GATEWAY_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${GW_ROLE}"
python3 "$SCRIPT_DIR/bedrock_infra_build.py"

# =============================================================================
# [Step 5] Agent 컨테이너 빌드 + Runtime 배포
# =============================================================================
echo ""
echo "[Step 5/6] Agent 컨테이너 빌드 + Runtime 배포..."

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1 || \
    aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" >/dev/null

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

aws ecr get-login-password --region "$REGION" | \
    $CONTAINER_CMD login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null 2>&1

sleep 5

for AGENT in agent_orchestrator agent_consultant agent_technician; do
    echo "  빌드+푸시: $AGENT..."
    $CONTAINER_CMD build --platform linux/arm64 -t "${ECR_REPO}:${AGENT}" --build-arg AGENT_FILE="${AGENT}.py" "$SCRIPT_DIR" -q
    $CONTAINER_CMD tag "${ECR_REPO}:${AGENT}" "${ECR_URI}:${AGENT}"
    $CONTAINER_CMD push "${ECR_URI}:${AGENT}" -q || (sleep 5 && $CONTAINER_CMD push "${ECR_URI}:${AGENT}" -q)
done
echo "  ✅ 컨테이너 배포 완료"

# Runtime 등록 + URL 저장
RUNTIME_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${GW_ROLE}"
COGNITO_CLIENT_ID=$(aws ssm get-parameter --name "${SSM_PREFIX}/cognito_client_id" --query 'Parameter.Value' --output text --region "$REGION")
COGNITO_UI_CLIENT_ID=$(aws ssm get-parameter --name "${SSM_PREFIX}/cognito_ui_client_id" --query 'Parameter.Value' --output text --region "$REGION")
COGNITO_DISCOVERY=$(aws ssm get-parameter --name "${SSM_PREFIX}/cognito_discovery_url" --query 'Parameter.Value' --output text --region "$REGION")
AUTH_CONFIG="{\"customJWTAuthorizer\":{\"allowedClients\":[\"${COGNITO_CLIENT_ID}\",\"${COGNITO_UI_CLIENT_ID}\"],\"discoveryUrl\":\"${COGNITO_DISCOVERY}\"}}"

# consultant + technician: A2A 프로토콜 (포트 9000)
for AGENT in agent_consultant agent_technician; do
    echo "  Runtime 등록: $AGENT (A2A)..."
    AGENT_ARN=$(aws bedrock-agentcore-control create-agent-runtime \
        --agent-runtime-name "$AGENT" \
        --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}:${AGENT}\"}}" \
        --role-arn "$RUNTIME_ROLE_ARN" \
        --network-configuration '{"networkMode":"PUBLIC"}' \
        --protocol-configuration '{"serverProtocol":"A2A"}' \
        --authorizer-configuration "$AUTH_CONFIG" \
        --environment-variables "AWS_REGION=${REGION}" \
        --region "$REGION" \
        --query 'agentRuntimeArn' --output text)
    AGENT_URL=$(python3 -c "from bedrock_agentcore.runtime import build_runtime_url; print(build_runtime_url('$AGENT_ARN'))")
    aws ssm put-parameter --name "${SSM_PREFIX}/${AGENT}_url" \
        --value "$AGENT_URL" --type String --overwrite --region "$REGION" >/dev/null
    echo "    URL 저장 완료"
done

# orchestrator: HTTP 프로토콜 (포트 8080) + requestHeaderAllowlist
echo "  Runtime 등록: agent_orchestrator (HTTP)..."
ORCH_ARN=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "agent_orchestrator" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}:agent_orchestrator\"}}" \
    --role-arn "$RUNTIME_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --authorizer-configuration "$AUTH_CONFIG" \
    --request-header-configuration '{"requestHeaderAllowlist":["Authorization"]}' \
    --environment-variables "AWS_REGION=${REGION}" \
    --region "$REGION" \
    --query 'agentRuntimeArn' --output text)
ORCH_URL=$(python3 -c "from bedrock_agentcore.runtime import build_runtime_url; print(build_runtime_url('$ORCH_ARN'))")
aws ssm put-parameter --name "${SSM_PREFIX}/agent_orchestrator_url" \
    --value "$ORCH_URL" --type String --overwrite --region "$REGION" >/dev/null
echo "    URL 저장 완료"

# =============================================================================
# 완료
# =============================================================================
echo ""
echo "============================================================"
echo "  ✅ 설정 완료!"
echo ""
echo "  UI 실행: streamlit run web_ui.py"
echo "  로그인:  demo / Demo1234!"
echo ""
echo "  SSM 확인: aws ssm get-parameters-by-path --path $SSM_PREFIX --recursive"
echo "============================================================"

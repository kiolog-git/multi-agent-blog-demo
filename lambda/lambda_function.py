"""
Lambda Function: MCP 도구 실행
===============================
AgentCore Gateway가 이 Lambda를 호출합니다.
Gateway에서 MCP 프로토콜로 요청이 들어오면, 도구 이름에 따라 분기합니다.

[블로그 매핑]
- Lambda (실행 함수) → 이 파일
- 외부 도구 (Slack, DB, 웹 검색) → 각 함수가 외부 API 호출
"""

from tool_consultant import lookup_product
from tool_technician import diagnose_issue


def get_named_parameter(event, name):
    """이벤트에서 파라미터 추출"""
    return event.get(name)


def lambda_handler(event, context):
    """
    AgentCore Gateway → Lambda 진입점.
    Gateway가 MCP 프로토콜로 도구를 호출하면 여기로 들어옵니다.
    """
    print(f"Event: {event}")

    # Gateway가 전달하는 도구 이름 추출
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]

    print(f"Tool: {tool_name}")

    if tool_name == "lookup_product":
        query = get_named_parameter(event, "query")

        if not query:
            return {"statusCode": 400, "body": "query 필요"}

        result = lookup_product(query)
        return {"statusCode": 200, "body": result}

    elif tool_name == "diagnose_issue":
        query = get_named_parameter(event, "query")

        if not query:
            return {"statusCode": 400, "body": "query 필요"}

        result = diagnose_issue(query)
        return {"statusCode": 200, "body": result}

    return {"statusCode": 400, "body": f"알 수 없는 도구: {tool_name}"}

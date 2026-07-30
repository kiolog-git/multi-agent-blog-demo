"""
기술자용 도구 — 기술 문제 진단 및 해결책 안내
==============================================
고객이 기술 문제를 물어보면 증상에 맞는 해결책을 안내합니다.
"""

# 기술 문제 해결 가이드 (데모용 샘플 데이터)
TROUBLESHOOTING_GUIDE = {
    "overheating": {
        "symptom": "기기가 뜨거워짐 / 과열",
        "diagnosis": "장시간 사용, 케이스 방열 불량, 백그라운드 앱 과다",
        "solution": [
            "케이스를 벗기고 10분간 식히기",
            "백그라운드 앱 모두 종료",
            "설정 → 배터리 → 전력 소모 높은 앱 확인",
            "충전 중 사용 자제",
            "지속되면 서비스센터 방문 (무상수리 가능)",
        ],
    },
    "battery": {
        "symptom": "배터리 빨리 닳음 / 충전 느림",
        "diagnosis": "배터리 노화, 고속충전 미지원 어댑터, 소프트웨어 문제",
        "solution": [
            "설정 → 배터리 → 사용량 확인",
            "정품 45W 충전기 사용 확인",
            "적응형 절전 모드 활성화",
            "배터리 보정: 완전 방전 후 100% 충전",
            "2년 이상 사용 시 배터리 교체 권장 (유상)",
        ],
    },
    "bluetooth": {
        "symptom": "블루투스 연결 불량 / 끊김",
        "diagnosis": "페어링 충돌, 거리 문제, 펌웨어 미업데이트",
        "solution": [
            "블루투스 껐다 켜기",
            "기존 페어링 삭제 후 재연결",
            "Galaxy Wearable 앱에서 펌웨어 업데이트",
            "다른 기기와의 간섭 확인 (Wi-Fi 공유기 근처 피하기)",
            "초기화: 이어버드를 케이스에 넣고 10초 터치패드 길게 누르기",
        ],
    },
}


def diagnose_issue(query: str) -> str:
    """
    기술 문제 증상으로 해결책을 조회합니다.
    증상 키워드에 매칭되는 진단 및 해결 가이드를 반환합니다.
    """
    query_lower = query.lower()

    for key, guide in TROUBLESHOOTING_GUIDE.items():
        if (key in query_lower
                or any(word in query_lower for word in guide["symptom"].split(" / "))):
            steps = "\n".join(f"  {i}. {s}" for i, s in enumerate(guide["solution"], 1))
            return (
                f"기술 진단 결과\n{'=' * 30}\n"
                f"증상: {guide['symptom']}\n"
                f"원인: {guide['diagnosis']}\n"
                f"\n해결 방법:\n{steps}\n"
                f"\n[출처: AgentCore Gateway → MCP → Lambda → 기술지원 DB 조회]"
            )

    return (
        f"'{query}'에 대한 기술 가이드를 찾을 수 없습니다.\n"
        f"지원 가능 주제: 과열, 배터리, 블루투스"
    )

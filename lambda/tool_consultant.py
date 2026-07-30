"""
상담사용 도구 — 제품 정보 조회 (보증, 스펙, 가격)
===================================================
고객이 제품에 대해 물어보면 보증 기간, 스펙, 반품 정책 등을 안내합니다.
"""

# 삼성 제품 카탈로그 (데모용 샘플 데이터)
PRODUCT_CATALOG = {
    "galaxy_s25_ultra": {
        "name": "Galaxy S25 Ultra",
        "category": "스마트폰",
        "price": "1,798,000원",
        "specs": {
            "display": "6.9인치 Dynamic AMOLED 2X, QHD+",
            "processor": "Snapdragon 8 Elite",
            "battery": "5,000mAh, 45W 고속충전",
        },
        "warranty": "1년 제조사 보증 (배터리 포함)",
        "return_policy": "개봉 후 14일 이내, 미개봉 30일 이내 반품 가능",
    },
    "galaxy_buds3_pro": {
        "name": "Galaxy Buds3 Pro",
        "category": "이어폰",
        "price": "359,000원",
        "specs": {
            "driver": "듀얼 드라이버 (플래너 + 다이나믹)",
            "anc": "인텔리전트 ANC + 주변음 허용",
            "battery": "본체 6시간, 케이스 포함 30시간",
        },
        "warranty": "1년 제조사 보증",
        "return_policy": "개봉 후 14일 이내 반품 가능 (이어팁 사용 시 불가)",
    },
    "galaxy_tab_s10_ultra": {
        "name": "Galaxy Tab S10 Ultra",
        "category": "태블릿",
        "price": "1,599,000원",
        "specs": {
            "display": "14.6인치 Dynamic AMOLED 2X, 120Hz",
            "processor": "MediaTek Dimensity 9300+",
            "battery": "11,200mAh, 45W 고속충전",
        },
        "warranty": "1년 제조사 보증",
        "return_policy": "개봉 후 14일 이내 반품 가능",
    },
}


def lookup_product(query: str) -> str:
    """
    제품명이나 카테고리로 제품 정보를 조회합니다.
    스펙, 가격, 보증, 반품 정책을 포함해서 반환합니다.
    """
    query_lower = query.lower().replace(" ", "_")

    # 매칭 시도 (제품명, 카테고리, 부분 매칭)
    for key, product in PRODUCT_CATALOG.items():
        if (key in query_lower
                or product["name"].lower().replace(" ", "_") in query_lower
                or product["category"] in query
                or any(word in query_lower for word in key.split("_"))):
            specs_text = "\n".join(f"  - {k}: {v}" for k, v in product["specs"].items())
            return (
                f"제품 정보\n{'=' * 30}\n"
                f"제품명: {product['name']}\n"
                f"카테고리: {product['category']}\n"
                f"가격: {product['price']}\n"
                f"\n스펙:\n{specs_text}\n"
                f"\n보증: {product['warranty']}\n"
                f"반품 정책: {product['return_policy']}\n"
                f"\n[출처: AgentCore Gateway → MCP → Lambda → 제품 DB 조회]"
            )

    return (
        f"'{query}'에 대한 제품 정보를 찾을 수 없습니다.\n"
        f"조회 가능한 제품: Galaxy S25 Ultra, Galaxy Buds3 Pro, Galaxy Tab S10 Ultra"
    )

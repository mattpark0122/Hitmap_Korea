"""reference/kospi-heatmap.html 의 섹터 매핑(SECT)을 옮긴 대체 매핑.

KRX 업종분류와 네이버 업종 모두에서 업종을 찾지 못한 종목에만 쓴다.
"""
from __future__ import annotations

import re

SECT = {
    "반도체": "삼성전자 삼성전자우 SK하이닉스 한미반도체 SK스퀘어 DB하이텍",
    "전자·부품": "삼성전기 LG전자 LG이노텍 LG디스플레이 삼성전기우",
    "2차전지": "LG에너지솔루션 삼성SDI 포스코퓨처엠 에코프로머티 엘앤에프",
    "에너지·화학": "LG화학 SK이노베이션 S-OIL 롯데케미칼 금호석유 한화솔루션 SKC GS",
    "자동차": "현대차 기아 현대모비스 현대글로비스 한온시스템 HL만도 현대차2우B 현대차우",
    "조선·방산": "HD현대중공업 HD한국조선해양 한화오션 삼성중공업 한화에어로스페이스 현대로템 한국항공우주 LIG넥스원 HD현대미포 한화시스템 풍산",
    "기계·전력": "HD현대일렉트릭 LSELECTRIC 효성중공업 두산에너빌리티 두산밥캣 LS 산일전기 HD현대마린솔루션 일진전기 대한전선",
    "금융": "KB금융 신한지주 하나금융지주 우리금융지주 메리츠금융지주 카카오뱅크 기업은행 삼성생명 삼성화재 한국금융지주 미래에셋증권 키움증권 DB손해보험 삼성증권 NH투자증권 BNK금융지주 JB금융지주 삼성카드 카카오페이 한화생명 현대해상",
    "바이오": "삼성바이오로직스 셀트리온 유한양행 SK바이오팜 한미약품 녹십자 SK바이오사이언스 HLB 대웅제약",
    "인터넷·IT": "NAVER 카카오 삼성에스디에스 크래프톤 엔씨소프트 넷마블 하이브 LGCNS 포스코DX",
    "통신": "SK텔레콤 KT LG유플러스",
    "지주": "SK LG 삼성물산 두산 HD현대 한화 CJ LS GS 효성 SK㈜ 한진칼",
    "철강·소재": "POSCO홀딩스 고려아연 현대제철 영풍 동국제강 포스코인터내셔널",
    "운송": "HMM 대한항공 팬오션 CJ대한통운",
    "유틸리티": "한국전력 한국가스공사 지역난방공사",
    "소비재": "KT&G 아모레퍼시픽 CJ제일제당 LG생활건강 오리온 삼양식품 이마트 롯데쇼핑 F&F 농심 하이트진로 코웨이",
    "건설": "삼성E&A 현대건설 GS건설 대우건설 DL이앤씨",
}


def norm(s: str) -> str:
    return re.sub(r"[\s()㈜]", "", str(s or "")).upper()


SECTOR_OF: dict[str, str] = {}
for _sec, _names in SECT.items():
    for _n in _names.split():
        SECTOR_OF.setdefault(norm(_n), _sec)


def lookup(name: str) -> str | None:
    return SECTOR_OF.get(norm(name))

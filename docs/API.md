# fundflow API

기본 주소: https://fundflow-api-scjl.onrender.com · Swagger UI `/docs` · 모두 GET, JSON

| 경로 | 설명 | 주요 파라미터 |
|---|---|---|
| `/health` | 상태 확인 | |
| `/api/meta` | 운용사·펀드·공시 수, 파싱 진행, 필드별 채움 현황 | |
| `/api/filters` | 유형·수탁사 선택지 | |
| `/api/overview` | KPI, 기간별 공시(종류별)·신규 펀드(유형별), 유형·위험등급 분포, 최근 신규 펀드 | `period`=month/quarter/year, `months` |
| `/api/funds` | 펀드 Monitor | `q`, `manager`, `asset_sub`, `trustee`, `risk`, `detail_ty`, `launched_from`, `launched_to`, `new_only`, `sort`, `order`, `page`, `size` |
| `/api/funds/{fund_key}` | 펀드 상세 + 공시 이력(DART 원문 링크) | fund_key는 URL 인코딩 |
| `/api/managers` | 운용사 검색 | `q`, `limit` |
| `/api/managers/{corp_code}` | 운용사 KPI, 유형 분포, 분기별 신규, 수탁사, 순자산 상위·최근 신규 펀드 | |
| `/api/league/managers` | 운용사 리그테이블 | `metric`=launches/filings/nav/net_flow, `from`, `to`, `asset_sub` |
| `/api/league/trustees` | 수탁사 리그테이블(신규 펀드 기준) | `from`, `to`, `asset_sub` |

단위: 금액 원, 보수 연 %, 날짜 YYYY-MM-DD.
`sort`: launch_dt, last_rcept_dt, nav, fee_total, fee_ter, risk_grade, fund_name, filings_count, net_flow

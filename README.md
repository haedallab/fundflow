# fundflow — DART 펀드공시 모니터

금융감독원 전자공시시스템(DART) [펀드공시 상세검색](https://dart.fss.or.kr/dsab005/main.do)에 올라오는
집합투자증권 공시(일괄신고서·증권신고서·투자설명서·증권발행실적보고서)를 2023년 10월 16일 이후분부터 모으고,
새로 올라오는 공시도 2시간마다 이어서 수집해 원문을 파싱하고,
펀드 · 운용사 · 수탁사 정보를 보여주는 서비스입니다. (KAIST 디지털금융 MBA 클라우드 실습 과제)

| 구성 | 위치 | 역할 |
|---|---|---|
| 화면 | Vercel `frontend/` → https://fundflow-dart.vercel.app | 개요 · 펀드 Monitor · 운용사 상세 · 운용사 리그테이블 · 수탁사 리그테이블 |
| API | Render `backend/` → https://fundflow-api-scjl.onrender.com/docs | FastAPI, 읽기 전용 |
| DB | Supabase PostgreSQL `supabase/migrations/` | managers · funds · filings · fetch_log |
| 수집 | GitHub Actions `.github/workflows/collect.yml` (2시간마다) | `collector/run.py` |

## 데이터 흐름

1. **공시 목록** — OpenAPI `list.json`을 `pblntf_detail_ty` G001 신탁형 · G002 회사형 · G003 합병으로 90일 구간씩 조회.
   보고서명 `일괄신고서(집합투자증권-신탁형)(펀드명)`에서 공시 종류와 펀드명을 뽑고, `운용사 고유번호:정규화 펀드명`을 펀드 키로 씁니다.
2. **파싱 대상** — 펀드별 최신 증권발행실적보고서, 최신 원본 일괄신고서·증권신고서(없으면 최신 투자설명서/정정 신고서).
3. **원문 파싱** — OpenAPI `document.xml`은 이 공시들의 원문을 주지 않아(014) DART 공시 화면의 목차 노드를 읽고
   필요한 절만 `report/viewer.do`로 받습니다 (요청 간격 0.5초).
   - 표지·요약: 펀드코드, 투자위험등급, 모집 증권 수
   - 종류 및 형태: 법적 형태, 운용자산(주식형·채권형…), 개방/추가형, 특수형태
   - 13. 보수 및 수수료: 클래스별 운용·판매·수탁·사무관리 보수, 총보수, 총보수·비용
   - 제4부 3. 기타 관련회사: 신탁업자(수탁사), 일반사무관리회사
   - 증권발행실적보고서: 기초·기말 순자산, 설정·해지 금액, 대상기간
4. **펀드 갱신** — 공시 목록 집계(신규 신고일, 공시 건수) + 파싱 값을 오래된 순으로 덮어써 `funds` 1행.

### 한계
- 정정 신고서 본문은 PDF라 표지·요약만 읽습니다. 그래서 **수탁사·보수는 2023년 10월 16일 이후 원본 신고서를 낸 펀드(주로 신규 펀드)만** 채워지고,
  수탁사 리그테이블은 신규 펀드 기준입니다.
- 순자산·설정·해지는 연 1회 제출되는 증권발행실적보고서 기준이라 시점이 펀드마다 다릅니다.
- 사모펀드는 DART 공시 대상이 아닙니다.

## 로컬 실행
```bash
pip install -r requirements.txt            # 수집기
cp .env.example .env                       # DART_API_KEY, DATABASE_URL 입력
python collector/run.py                    # RUN_MINUTES=10 PARSE_LIMIT=20 처럼 줄여서 시험 가능
PROBE_ONLY=1 DEBUG_DIR=probe python collector/run.py   # DB 없이 파서만 점검

cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && python -m http.server 5173  # config.js의 API 주소 확인
```

문서: [API](docs/API.md) · [DB](docs/DB.md)

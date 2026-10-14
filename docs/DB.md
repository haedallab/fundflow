# DB (Supabase PostgreSQL)

스키마: `supabase/migrations/20261014173218_fund_schema.sql` (main 병합 시 Supabase GitHub 연동이 적용)

```
managers (corp_code PK, name)                       운용사 = 공시 제출인(집합투자업자)
   │1
   │n
funds (fund_key PK = corp_code:정규화 펀드명)        펀드 1행
   │1   fund_code, legal_form, asset_class, asset_sub, open_end, additional, special[],
   │    risk_grade, trustee, admin_co, fee_* (대표 클래스), classes jsonb (전체 클래스),
   │    nav, nav_prev, subs_amt, redeem_amt, nav_period, launch_dt, filings_count
   │n
filings (rcept_no PK)                               DART 펀드공시 1건 = 1행, 수집 대기열 겸 이력
        kind SHELF/REG/PROSPECTUS/ISSUE/OTHER, is_amend, rcept_dt,
        status skip/pending/done/error, parsed jsonb (원문에서 뽑은 값)
fetch_log (task PK)                                 공시 목록 구간별 수집 기록
```

- `funds.launch_dt` = 기간 내 최초 원본 일괄신고서·증권신고서 접수일 (신규 펀드 판정)
- `funds`는 `filings`에서 다시 만들 수 있는 파생 테이블입니다 (Actions 수동 실행 `rebuild=1`).
- RLS 켜짐, 정책 없음 → 외부 키로는 접근 불가, API 서버(DB 직접 접속)만 읽습니다.

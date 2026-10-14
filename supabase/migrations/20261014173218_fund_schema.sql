-- fundflow v2: DART 펀드공시(집합투자증권) 서비스 스키마
-- 출처: 금융감독원 DART 공시검색 pblntf_ty=G (G001 신탁형 · G002 회사형 · G003 합병)

-- 운용사 (공시 제출인 = 집합투자업자)
create table if not exists managers (
    corp_code   char(8)     primary key,          -- DART 고유번호
    name        text        not null,
    updated_at  timestamptz not null default now()
);

-- 펀드 (운용사 + 정규화한 펀드명으로 식별)
create table if not exists funds (
    fund_key        text        primary key,      -- '<corp_code>:<정규화 펀드명>'
    corp_code       char(8)     not null references managers(corp_code),
    fund_name       text        not null,         -- 가장 최근 공시의 펀드명
    fund_code       text,                         -- 금융투자협회 대표 펀드코드
    detail_ty       char(4),                      -- G001 신탁형 / G002 회사형 / G003 합병
    legal_form      text,                         -- 투자신탁 / 투자회사 등
    asset_class     text,                         -- 증권 / 부동산 / 특별자산 / 혼합자산 / 단기금융
    asset_sub       text,                         -- 주식형 / 채권형 / 혼합주식형 / 혼합채권형 / 재간접형 / 파생형 …
    open_end        boolean,                      -- 개방형
    additional      boolean,                      -- 추가형
    special         text[],                       -- 특수형태: 모자형, 종류형, 상장지수, 전환형 …
    risk_grade      smallint check (risk_grade between 1 and 6),
    risk_label      text,
    offer_units     text,                         -- 모집(매출) 증권의 수 문구
    trustee         text,                         -- 신탁업자(수탁회사)
    admin_co        text,                         -- 일반사무관리회사
    fee_mgmt        numeric(8,4),                 -- 대표 클래스 보수(연 %)
    fee_sales       numeric(8,4),
    fee_trust       numeric(8,4),
    fee_admin       numeric(8,4),
    fee_total       numeric(8,4),
    fee_ter         numeric(8,4),                 -- 총보수·비용
    fee_total_min   numeric(8,4),                 -- 클래스 중 최저·최고 총보수
    fee_total_max   numeric(8,4),
    class_count     smallint,
    classes         jsonb,                        -- [{class, mgmt, sales, trust, admin, total, ter}]
    nav             numeric(22,0),                -- 증권발행실적보고서 기말 순자산(원)
    nav_prev        numeric(22,0),                -- 기초 순자산
    subs_amt        numeric(22,0),                -- 대상기간 설정 금액
    redeem_amt      numeric(22,0),                -- 대상기간 해지 금액
    nav_period      text,                         -- 대상기간 원문
    nav_rcept_no    varchar(14),
    launch_dt       date,                         -- 기간 내 최초 원본 신고서(일괄·증권신고서) 접수일
    first_rcept_dt  date,
    last_rcept_dt   date,
    filings_count   integer     not null default 0,
    updated_at      timestamptz not null default now()
);
create index if not exists ix_funds_corp   on funds (corp_code);
create index if not exists ix_funds_launch on funds (launch_dt);
create index if not exists ix_funds_trust  on funds (trustee);

-- 공시 (수집 대기열 겸 이력)
create table if not exists filings (
    rcept_no    varchar(14) primary key,
    corp_code   char(8)     not null references managers(corp_code),
    fund_key    text        not null,
    fund_name   text,
    report_nm   text        not null,
    kind        varchar(12) not null,             -- SHELF 일괄신고서 / REG 증권신고서 / PROSPECTUS 투자설명서 / ISSUE 증권발행실적보고서 / OTHER
    is_amend    boolean     not null default false,  -- [기재정정] 등 정정 공시
    detail_ty   char(4),
    rcept_dt    date        not null,
    status      varchar(10) not null default 'skip',  -- 원문 파싱 대상이면 pending → done / error, 아니면 skip
    parsed      jsonb,                                -- 원문에서 뽑은 값 (펀드 정보는 이 값들을 오래된 순으로 덮어써 만듦)
    error       text,
    parsed_at   timestamptz
);
create index if not exists ix_filings_fund on filings (fund_key);
create index if not exists ix_filings_kind on filings (kind, status);
create index if not exists ix_filings_dt   on filings (rcept_dt);

-- 수집 진행 기록
create table if not exists fetch_log (
    task        text        primary key,
    status      text        not null,
    fetched_at  timestamptz not null default now()
);

comment on table managers is '집합투자업자(운용사)';
comment on table funds    is '펀드 1개 = 1행. 공시 목록·투자설명서 표지·증권발행실적보고서·원본 신고서 본문을 합친 결과';
comment on table filings  is 'DART 펀드공시 목록과 원문 처리 상태';

alter table managers  enable row level security;
alter table funds     enable row level security;
alter table filings   enable row level security;
alter table fetch_log enable row level security;

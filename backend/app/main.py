"""
fundflow API — DART 펀드공시(집합투자증권) 기반 펀드·운용사·수탁사 정보

Swagger UI: /docs   ·   ReDoc: /redoc
"""
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import pool, fetch_all, fetch_one


@asynccontextmanager
async def lifespan(app):
    pool.open()
    yield
    pool.close()


app = FastAPI(
    title="fundflow API",
    version="3.0.0",
    description=(
        "금융감독원 전자공시시스템(DART) 펀드공시 — 일괄신고서·증권신고서·투자설명서·증권발행실적보고서 — 를 "
        "수집·파싱해 펀드, 운용사(집합투자업자), 수탁사(신탁업자) 정보를 제공합니다.\n\n"
        "- 금액 단위: 원 (KRW) · 보수: 연 % · 날짜: YYYY-MM-DD\n"
        "- 공시 종류: SHELF 일괄신고서, REG 증권신고서, PROSPECTUS 투자설명서, ISSUE 증권발행실적보고서\n"
        "- 출처: DART OpenAPI 공시목록(list.json) + DART 공시 원문 뷰어"),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "meta", "description": "상태·필터 값"},
        {"name": "overview", "description": "기간별 공시·신규 펀드 추이"},
        {"name": "funds", "description": "펀드 목록·상세"},
        {"name": "managers", "description": "운용사 검색·상세"},
        {"name": "league", "description": "운용사·수탁사 리그테이블"},
    ],
)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["GET"], allow_headers=["*"])

KINDS = {"SHELF": "일괄신고서", "REG": "증권신고서", "PROSPECTUS": "투자설명서",
         "ISSUE": "증권발행실적보고서", "OTHER": "기타"}
DETAIL = {"G001": "신탁형", "G002": "회사형", "G003": "합병"}
Period = Literal["month", "quarter", "year"]


def dart_url(rcp: Optional[str]) -> Optional[str]:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}" if rcp else None


def default_from() -> date:
    return date.today() - timedelta(days=365)


# ── meta ────────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
def health():
    fetch_one("select 1 as ok")
    return {"status": "ok"}


@app.get("/api/meta", tags=["meta"], summary="데이터 현황")
def meta():
    r = fetch_one("""
        select (select count(*) from managers) managers, (select count(*) from funds) funds,
               (select count(*) from filings) filings,
               (select count(*) from filings where status = 'done') parsed,
               (select count(*) from filings where status = 'pending') pending,
               (select min(rcept_dt) from filings) first_dt, (select max(rcept_dt) from filings) last_dt,
               (select max(fetched_at) from fetch_log) updated_at,
               (select count(*) from funds where trustee is not null) with_trustee,
               (select count(*) from funds where fee_total is not null) with_fee,
               (select count(*) from funds where nav is not null) with_nav,
               (select count(*) from funds where risk_grade is not null) with_risk""")
    return {**r, "source": "금융감독원 전자공시시스템 DART (https://dart.fss.or.kr/dsab005/main.do 펀드공시)"}


@app.get("/api/filters", tags=["meta"], summary="필터 선택지")
def filters():
    return {
        "asset_sub": [r["v"] for r in fetch_all(
            "select asset_sub v, count(*) n from funds where asset_sub is not null group by 1 order by 2 desc")],
        "trustees": [r["v"] for r in fetch_all(
            "select trustee v, count(*) n from funds where trustee is not null group by 1 order by 2 desc")],
        "kinds": KINDS, "detail_ty": DETAIL,
    }


# ── overview ────────────────────────────────────────────────────────────
@app.get("/api/overview", tags=["overview"], summary="개요: KPI·기간별 공시/신규 펀드 추이·분포")
def overview(period: Period = "month", months: int = Query(36, ge=3, le=60)):
    since = date.today().replace(day=1) - timedelta(days=31 * (months - 1))
    since = since.replace(day=1)
    p = {"period": period, "since": since}
    kpi = fetch_one("""
        select count(*) funds,
               count(*) filter (where launch_dt >= current_date - 365) new_12m,
               count(distinct corp_code) managers,
               sum(nav) nav, sum(subs_amt) subs, sum(redeem_amt) redeem,
               count(*) filter (where nav is not null) nav_funds,
               avg(fee_total) filter (where fee_total is not null) fee_avg,
               avg(fee_ter) filter (where fee_ter is not null) ter_avg
        from funds""")
    kpi["filings_12m"] = fetch_one(
        "select count(*) n from filings where rcept_dt >= current_date - 365")["n"]
    filings = fetch_all("""
        select to_char(date_trunc(%(period)s, rcept_dt), 'YYYY-MM-DD') bucket, kind, count(*) n
        from filings where rcept_dt >= %(since)s group by 1, 2 order by 1""", p)
    launches = fetch_all("""
        select to_char(date_trunc(%(period)s, launch_dt), 'YYYY-MM-DD') bucket,
               coalesce(asset_sub, '기타') asset_sub, count(*) n
        from funds where launch_dt >= %(since)s group by 1, 2 order by 1""", p)
    by_asset = fetch_all("""
        select coalesce(asset_sub, '미분류') asset_sub, count(*) funds, sum(nav) nav,
               count(*) filter (where launch_dt >= current_date - 365) new_12m
        from funds group by 1 order by 2 desc""")
    by_risk = fetch_all("""
        select risk_grade, max(risk_label) risk_label, count(*) funds
        from funds where risk_grade is not null group by 1 order by 1""")
    recent = fetch_all("""
        select f.fund_key, f.fund_name, m.name manager, f.asset_sub, f.launch_dt, f.trustee, f.fee_total,
               f.risk_grade
        from funds f join managers m using (corp_code)
        where f.launch_dt is not null order by f.launch_dt desc, f.fund_name limit 10""")
    return {"kpi": kpi, "filings": filings, "launches": launches, "by_asset": by_asset,
            "by_risk": by_risk, "recent_launches": recent, "since": since, "period": period}


# ── funds ───────────────────────────────────────────────────────────────
SORTS = {"launch_dt": "f.launch_dt", "last_rcept_dt": "f.last_rcept_dt", "nav": "f.nav",
         "fee_total": "f.fee_total", "fee_ter": "f.fee_ter", "risk_grade": "f.risk_grade",
         "fund_name": "f.fund_name", "filings_count": "f.filings_count", "net_flow": "(f.subs_amt - f.redeem_amt)"}


@app.get("/api/funds", tags=["funds"], summary="펀드 Monitor: 검색·필터·정렬·페이지")
def funds(q: Optional[str] = Query(None, description="펀드명·펀드코드 검색"),
          manager: Optional[str] = Query(None, description="운용사 corp_code"),
          asset_sub: Optional[str] = None, trustee: Optional[str] = None,
          risk: Optional[int] = Query(None, ge=1, le=6),
          detail_ty: Optional[Literal["G001", "G002", "G003"]] = None,
          launched_from: Optional[date] = None, launched_to: Optional[date] = None,
          new_only: bool = Query(False, description="수집 시작일(2023-10-16) 이후 신규 신고 펀드만"),
          sort: Literal[tuple(SORTS)] = "last_rcept_dt", order: Literal["asc", "desc"] = "desc",
          page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    w, p = ["true"], {}
    if q:
        w.append("(f.fund_name ilike %(q)s or f.fund_code = upper(%(qc)s) or m.name ilike %(q)s)")
        p.update(q=f"%{q.strip()}%", qc=q.strip())
    for col, val in (("f.corp_code", manager), ("f.asset_sub", asset_sub), ("f.trustee", trustee),
                     ("f.risk_grade", risk), ("f.detail_ty", detail_ty)):
        if val is not None:
            k = col.split(".")[1]
            w.append(f"{col} = %({k})s")
            p[k] = val
    if launched_from:
        w.append("f.launch_dt >= %(lf)s"); p["lf"] = launched_from
    if launched_to:
        w.append("f.launch_dt <= %(lt)s"); p["lt"] = launched_to
    if new_only:
        w.append("f.launch_dt is not null")
    where = " and ".join(w)
    total = fetch_one(f"select count(*) n from funds f join managers m using (corp_code) where {where}", p)["n"]
    p.update(limit=size, offset=(page - 1) * size)
    items = fetch_all(f"""
        select f.fund_key, f.fund_name, f.fund_code, f.corp_code, m.name manager, f.detail_ty,
               f.legal_form, f.asset_class, f.asset_sub, f.special, f.risk_grade, f.risk_label,
               f.trustee, f.fee_total, f.fee_ter, f.fee_total_min, f.fee_total_max, f.class_count,
               f.nav, f.subs_amt, f.redeem_amt, f.launch_dt, f.last_rcept_dt, f.filings_count
        from funds f join managers m using (corp_code)
        where {where}
        order by {SORTS[sort]} {order} nulls last, f.fund_key
        limit %(limit)s offset %(offset)s""", p)
    return {"total": total, "page": page, "size": size, "items": items}


@app.get("/api/funds/{fund_key:path}", tags=["funds"], summary="펀드 상세와 공시 이력")
def fund(fund_key: str):
    f = fetch_one("""select f.*, m.name manager from funds f join managers m using (corp_code)
                     where f.fund_key = %(k)s""", {"k": fund_key})
    if not f:
        raise HTTPException(404, "펀드를 찾을 수 없습니다")
    fl = fetch_all("""select rcept_no, rcept_dt, report_nm, kind, is_amend, status
                      from filings where fund_key = %(k)s order by rcept_dt desc, rcept_no desc""",
                   {"k": fund_key})
    for r in fl:
        r["url"] = dart_url(r["rcept_no"])
    f["nav_url"] = dart_url(f.get("nav_rcept_no"))
    return {"fund": f, "filings": fl}


# ── managers ────────────────────────────────────────────────────────────
@app.get("/api/managers", tags=["managers"], summary="운용사 검색")
def managers(q: Optional[str] = None, limit: int = Query(30, ge=1, le=300)):
    return fetch_all("""
        select m.corp_code, m.name, count(f.*) funds,
               count(f.*) filter (where f.launch_dt >= current_date - 365) new_12m, sum(f.nav) nav
        from managers m left join funds f using (corp_code)
        where %(q)s::text is null or m.name ilike '%%' || %(q)s || '%%'
        group by 1, 2 order by funds desc limit %(limit)s""", {"q": q, "limit": limit})


@app.get("/api/managers/{corp_code}", tags=["managers"], summary="운용사 상세")
def manager(corp_code: str):
    p = {"c": corp_code}
    m = fetch_one("select corp_code, name from managers where corp_code = %(c)s", p)
    if not m:
        raise HTTPException(404, "운용사를 찾을 수 없습니다")
    m["kpi"] = fetch_one("""
        select count(*) funds, count(*) filter (where launch_dt is not null) launched_3y,
               count(*) filter (where launch_dt >= current_date - 365) new_12m,
               sum(nav) nav, sum(subs_amt) subs, sum(redeem_amt) redeem,
               avg(fee_total) fee_avg, avg(fee_ter) ter_avg, avg(risk_grade) risk_avg,
               (select count(*) from filings where corp_code = %(c)s) filings
        from funds where corp_code = %(c)s""", p)
    rank = fetch_one("""
        select r from (select corp_code, rank() over (order by count(*) desc) r
                       from funds where launch_dt is not null group by 1) x where corp_code = %(c)s""", p)
    m["kpi"]["launch_rank"] = rank["r"] if rank else None
    m["by_asset"] = fetch_all("""
        select coalesce(asset_sub, '미분류') asset_sub, count(*) funds, sum(nav) nav
        from funds where corp_code = %(c)s group by 1 order by 2 desc""", p)
    m["launches"] = fetch_all("""
        select to_char(date_trunc('quarter', launch_dt), 'YYYY-MM-DD') bucket, count(*) n
        from funds where corp_code = %(c)s and launch_dt is not null group by 1 order by 1""", p)
    m["trustees"] = fetch_all("""
        select trustee, count(*) funds from funds where corp_code = %(c)s and trustee is not null
        group by 1 order by 2 desc""", p)
    m["top_nav"] = fetch_all("""
        select fund_key, fund_name, asset_sub, nav, subs_amt, redeem_amt, nav_period, fee_total
        from funds where corp_code = %(c)s and nav is not null order by nav desc limit 10""", p)
    m["recent"] = fetch_all("""
        select fund_key, fund_name, asset_sub, launch_dt, trustee, fee_total, fee_ter, risk_grade
        from funds where corp_code = %(c)s and launch_dt is not null
        order by launch_dt desc, fund_name limit 15""", p)
    return m


# ── league ──────────────────────────────────────────────────────────────
@app.get("/api/league/managers", tags=["league"], summary="운용사 리그테이블")
def league_managers(metric: Literal["launches", "filings", "nav", "net_flow"] = "launches",
                    date_from: Optional[date] = Query(None, alias="from"),
                    date_to: Optional[date] = Query(None, alias="to"),
                    asset_sub: Optional[str] = None, limit: int = Query(30, ge=1, le=200)):
    p = {"f": date_from or default_from(), "t": date_to or date.today(), "a": asset_sub, "limit": limit}
    asset = "(%(a)s::text is null or f.asset_sub = %(a)s)"
    if metric == "filings":
        sql = f"""select m.corp_code, m.name, count(*)::numeric value, count(distinct l.fund_key) funds
                  from filings l join managers m using (corp_code) join funds f using (fund_key)
                  where l.rcept_dt between %(f)s and %(t)s and {asset} group by 1, 2"""
    elif metric == "launches":
        sql = f"""select m.corp_code, m.name, count(*)::numeric value, count(*) funds,
                         avg(f.fee_total) fee_avg, avg(f.fee_ter) ter_avg
                  from funds f join managers m using (corp_code)
                  where f.launch_dt between %(f)s and %(t)s and {asset} group by 1, 2"""
    else:
        expr = "sum(f.nav)" if metric == "nav" else "sum(f.subs_amt - f.redeem_amt)"
        sql = f"""select m.corp_code, m.name, {expr} value, count(*) funds
                  from funds f join managers m using (corp_code)
                  where f.nav is not null and {asset} group by 1, 2"""
    rows = fetch_all(f"""with x as ({sql}) select *, rank() over (order by value desc nulls last) rank,
                         value / nullif(sum(value) over (), 0) * 100 share
                         from x where value is not null order by rank limit %(limit)s""", p)
    tot = fetch_one(f"with x as ({sql}) select sum(value) total, count(*) n from x", p)
    return {"metric": metric, "from": p["f"], "to": p["t"], "asset_sub": asset_sub,
            "total": tot["total"], "participants": tot["n"], "rows": rows}


@app.get("/api/league/trustees", tags=["league"], summary="수탁사(신탁업자) 리그테이블 — 신규 펀드 기준")
def league_trustees(date_from: Optional[date] = Query(None, alias="from"),
                    date_to: Optional[date] = Query(None, alias="to"),
                    asset_sub: Optional[str] = None, limit: int = Query(30, ge=1, le=100)):
    p = {"f": date_from or default_from(), "t": date_to or date.today(), "a": asset_sub, "limit": limit}
    base = """from funds f join managers m using (corp_code)
              where f.trustee is not null and f.launch_dt between %(f)s and %(t)s
                and (%(a)s::text is null or f.asset_sub = %(a)s)"""
    rows = fetch_all(f"""
        with x as (select f.trustee, count(*) funds, count(distinct f.corp_code) managers,
                          avg(f.fee_trust) fee_trust_avg, sum(f.nav) nav {base} group by 1)
        select *, rank() over (order by funds desc) rank,
               funds::numeric / nullif(sum(funds) over (), 0) * 100 share
        from x order by rank limit %(limit)s""", p)
    tops = fetch_all(f"""
        select trustee, name, n from (
          select f.trustee, m.name, count(*) n,
                 row_number() over (partition by f.trustee order by count(*) desc) rn
          {base} group by 1, 2) y where rn <= 3""", p)
    for r in rows:
        r["top_managers"] = [{"name": t["name"], "funds": t["n"]} for t in tops if t["trustee"] == r["trustee"]]
    tot = fetch_one(f"select count(*) n, count(distinct f.trustee) t {base}", p)
    cover = fetch_one("""select count(*) n from funds where launch_dt between %(f)s and %(t)s
                         and (%(a)s::text is null or asset_sub = %(a)s)""", p)
    return {"from": p["f"], "to": p["t"], "asset_sub": asset_sub, "total_funds": tot["n"],
            "trustees": tot["t"], "launched_funds": cover["n"], "rows": rows}

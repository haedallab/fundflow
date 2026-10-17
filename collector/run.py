"""DART 펀드공시 수집기 (GitHub Actions에서 3시간마다 실행)

  A. 공시 목록   OpenAPI list.json, 최근 3년을 90일 구간으로 나눠 G001 신탁형 · G002 회사형 · G003 합병
                 → managers, filings (펀드명은 보고서명 끝 괄호에서 추출)
  B. 대상 선정   펀드별 최신 증권발행실적보고서 · 최신 원본 신고서(일괄·증권신고서)
                 · 원본이 없으면 최신 투자설명서/정정 신고서 → status = pending
  C. 원문 파싱   DART 웹 뷰어로 필요한 절만 읽어 filings.parsed 에 저장 (최신 공시부터, 시간 한도까지)
  D. 펀드 갱신   filings 집계 + parsed 값을 오래된 순으로 덮어써 funds 1행 생성

환경변수: DART_API_KEY, DATABASE_URL, DART_BUDGET(기본 15000), RUN_MINUTES(기본 100),
          YEARS(기본 3), DEBUG_DIR(지정 시 파싱 원문 일부 저장), PARSE_LIMIT(테스트용 최대 건수)
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import unquote

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

sys.path.insert(0, os.path.dirname(__file__))
from dart import BudgetExceeded, Dart  # noqa: E402
from parse import PARSER_VERSION, Blocked, Viewer, company, parse_filing  # noqa: E402

load_dotenv()
T0 = time.time()
RUN_MIN = float(os.environ.get("RUN_MINUTES", "100"))
YEARS = int(os.environ.get("YEARS", "3"))
DEBUG_DIR = os.environ.get("DEBUG_DIR")
LIST_MIN = float(os.environ.get("LIST_MINUTES", "50"))
PARSE_LIMIT = int(os.environ.get("PARSE_LIMIT", "0") or 0)


def secret_key() -> str:
    raw = os.environ.get("DART_API_KEY", "")
    keys = re.findall(r"[0-9a-fA-F]{40}", raw)
    if not keys:
        sys.exit("환경변수 DART_API_KEY 에 40자리 인증키가 없습니다")
    return keys[0]


def db_kwargs() -> dict:
    """DATABASE_URL → 접속 인자. 비밀번호의 특수문자(@ # / 등)를 인코딩하지 않고 넣어도 동작하도록 직접 분해"""
    u = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    u = re.sub(r"^DATABASE_URL\s*=\s*", "", u).strip().strip('"').strip("'")
    m = re.match(r"^postgres(?:ql)?://(.*)$", u, re.S)
    if not m:
        sys.exit("환경변수 DATABASE_URL 이 postgresql:// 로 시작하지 않습니다")
    cred, _, rest = m.group(1).rpartition("@")
    user, _, pw = cred.partition(":")
    hostport, _, db = rest.partition("/")
    host, _, port = hostport.partition(":")
    db = db.split("?")[0] or "postgres"
    if re.fullmatch(r"\[.*\]", pw):           # [YOUR-PASSWORD] 자리에 괄호까지 남긴 경우
        pw = pw[1:-1]
    if re.search(r"%[0-9A-Fa-f]{2}", pw):
        pw = unquote(pw)
    print(f"  DB 접속: user={user} host={host} port={port or 5432} db={db} "
          f"password={len(pw)}자{' (YOUR-PASSWORD 그대로)' if 'YOUR-PASSWORD' in pw.upper() else ''}")
    return {"user": unquote(user), "password": pw, "host": host, "port": int(port or 5432), "dbname": db}


def minutes() -> float:
    return (time.time() - T0) / 60


# ── 보고서명 → 종류·펀드명 ──────────────────────────────────────────────
def classify(report_nm: str) -> tuple[str, bool, str | None]:
    nm = report_nm.strip()
    amend = nm.startswith("[")
    body = re.sub(r"^\[[^\]]*\]\s*", "", nm)
    if "증권발행실적" in body:
        kind = "ISSUE"
    elif "투자설명서" in body:
        kind = "PROSPECTUS"
    elif body.startswith("일괄신고서"):
        kind = "SHELF"
    elif body.startswith("증권신고서"):
        kind = "REG"
    else:
        kind = "OTHER"
    m = re.search(r"\(집합투자증권[^)]*\)\s*\((.+)\)\s*$", body) or re.search(r"\)\s*\((.+)\)\s*$", body)
    name = m.group(1).strip() if m else None
    return kind, amend, name


def norm_name(name: str) -> str:
    s = name.translate(str.maketrans("[]{}（）［］", "()()()()"))
    return re.sub(r"\s+", "", s).upper()


# ── A. 공시 목록 ────────────────────────────────────────────────────────
def windows(today: date):
    start = today - timedelta(days=365 * YEARS)
    d = today
    while d > start:
        s = max(start, d - timedelta(days=89))
        yield s, d
        d = s - timedelta(days=1)


def collect_list(con, dart: Dart):
    today = date.today()
    done = {r[0] for r in con.execute("select task from fetch_log where status = 'done'")}
    for ty in ("G001", "G002", "G003"):
        for s, e in windows(today):
            task = f"list:{ty}:{s:%Y%m%d}:{e:%Y%m%d}"
            closed = e < today - timedelta(days=7)          # 1주일 지난 구간은 다시 받지 않음
            if closed and task in done:
                continue
            if minutes() > LIST_MIN:
                print(f"  목록 수집 시간 한도 {LIST_MIN:.0f}분 도달, 남은 구간은 다음 실행에서")
                return
            n = page = 0
            total = 1
            while page * 100 < total:
                page += 1
                j = dart.json("list", pblntf_detail_ty=ty, bgn_de=f"{s:%Y%m%d}", end_de=f"{e:%Y%m%d}",
                              page_no=page, page_count=100, sort="date", sort_mth="desc")
                if j.get("status") == "013":
                    break
                total = int(j.get("total_count") or 0)
                save_list(con, ty, j.get("list", []))
                n += len(j.get("list", []))
            con.execute("insert into fetch_log (task, status) values (%s, %s) "
                        "on conflict (task) do update set status = excluded.status, fetched_at = now()",
                        (task, "done" if closed else "open"))
            print(f"  목록 {ty} {s}~{e}: {n}건 (DART 호출 {dart.calls})")


def save_list(con, ty, rows):
    mgr, fil = {}, []
    for r in rows:
        kind, amend, name = classify(r.get("report_nm", ""))
        if not name or not r.get("corp_code"):
            continue
        mgr[r["corp_code"]] = r.get("corp_name") or r["corp_code"]
        fil.append((r["rcept_no"], r["corp_code"], f'{r["corp_code"]}:{norm_name(name)}', name,
                    re.sub(r"\s+", " ", r["report_nm"]).strip(), kind, amend, ty,
                    datetime.strptime(r["rcept_dt"], "%Y%m%d").date()))
    with con.cursor() as cur:
        cur.executemany("insert into managers (corp_code, name) values (%s, %s) on conflict (corp_code) "
                        "do update set name = excluded.name, updated_at = now()", list(mgr.items()))
        cur.executemany("""insert into filings (rcept_no, corp_code, fund_key, fund_name, report_nm, kind,
                               is_amend, detail_ty, rcept_dt)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           on conflict (rcept_no) do update set report_nm = excluded.report_nm,
                               fund_name = excluded.fund_name, kind = excluded.kind""", fil)


# ── B. 원문 파싱 대상 ───────────────────────────────────────────────────
TARGET_SQL = """
with g as (
  select rcept_no, fund_key, rcept_dt,
         case when kind = 'ISSUE' then 'I'
              when kind in ('SHELF','REG') and not is_amend then 'O'
              when kind in ('PROSPECTUS','SHELF','REG') then 'C' end grp
  from filings
), latest as (
  select distinct on (fund_key, grp) rcept_no, fund_key, grp
  from g where grp is not null
  order by fund_key, grp, rcept_dt desc, rcept_no desc
)
update filings f set status = 'pending'
from latest l
where f.rcept_no = l.rcept_no and f.status = 'skip'
  and (l.grp <> 'C' or not exists (select 1 from latest o where o.fund_key = l.fund_key and o.grp = 'O'))
"""


# ── C. 원문 파싱 ────────────────────────────────────────────────────────
def parse_pending(con, viewer: Viewer) -> set:
    rows = con.execute("""select rcept_no, fund_key, kind, is_amend from filings
                          where status = 'pending'
                          order by (kind in ('SHELF','REG') and not is_amend) desc, kind = 'ISSUE' desc,
                                   rcept_dt desc, rcept_no desc
                          limit %s""", (PARSE_LIMIT or 100000,)).fetchall()
    print(f"[C] 원문 파싱 대기 {len(rows)}건")
    touched, ok, bad, dumped = set(), 0, 0, 0
    for rcp, key, kind, amend in rows:
        if minutes() > RUN_MIN:
            print(f"  시간 한도 {RUN_MIN:.0f}분 도달, 다음 실행에서 이어감")
            break
        dbg = {} if DEBUG_DIR and dumped < 12 else None
        try:
            p = parse_filing(viewer, rcp, kind, amend, dbg)
            con.execute("update filings set status='done', parsed=%s, error=null, parsed_at=now() "
                        "where rcept_no=%s", (Jsonb(p), rcp))
            ok += 1
        except Blocked as e:
            print("  ※", e)
            break
        except Exception as e:  # noqa: BLE001 — 한 건 실패는 기록만 하고 계속
            con.execute("update filings set status='error', error=%s, parsed_at=now() where rcept_no=%s",
                        (f"{type(e).__name__}: {e}"[:500], rcp))
            bad += 1
            p = {}
        touched.add(key)
        if dbg is not None:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            with open(f"{DEBUG_DIR}/{rcp}_{kind}{'_amend' if amend else ''}.json", "w", encoding="utf-8") as f:
                json.dump({"parsed": p, "nodes": dbg.get("nodes"),
                           "html": [[t, h[:200000]] for t, h in dbg.get("html", [])]}, f, ensure_ascii=False)
            dumped += 1
        if (ok + bad) % 50 == 0:
            print(f"  파싱 {ok + bad}건 (성공 {ok}, 실패 {bad}, 웹 요청 {viewer.calls}, {minutes():.0f}분)")
    print(f"  파싱 완료 {ok}건, 실패 {bad}건, 웹 요청 {viewer.calls}회")
    return touched


# ── D. 펀드 갱신 ────────────────────────────────────────────────────────
AGG_SQL = """
insert into funds (fund_key, corp_code, fund_name, detail_ty, launch_dt, first_rcept_dt, last_rcept_dt,
                   filings_count, updated_at)
select fund_key,
       (array_agg(corp_code order by rcept_dt desc, rcept_no desc))[1],
       (array_agg(fund_name order by rcept_dt desc, rcept_no desc))[1],
       (array_agg(detail_ty order by rcept_dt desc, rcept_no desc))[1],
       min(rcept_dt) filter (where kind in ('SHELF','REG') and not is_amend),
       min(rcept_dt), max(rcept_dt), count(*), now()
from filings group by fund_key
on conflict (fund_key) do update set
  corp_code = excluded.corp_code, fund_name = excluded.fund_name, detail_ty = excluded.detail_ty,
  launch_dt = excluded.launch_dt, first_rcept_dt = excluded.first_rcept_dt,
  last_rcept_dt = excluded.last_rcept_dt, filings_count = excluded.filings_count, updated_at = now()
"""

# 원문에서 유형을 못 읽은 펀드는 펀드명으로 추정
NAME_SUB_SQL = """
update funds set asset_sub = case
    when fund_name ~ '(MMF|머니마켓|단기금융)' then 'MMF'
    when fund_name ~ '(주식혼합|혼합주식)' then '혼합주식형'
    when fund_name ~ '(채권혼합|혼합채권)' then '혼합채권형'
    when fund_name ~ '재간접' then '재간접형'
    when fund_name ~ '(파생|ELS|DLS)' then '파생형'
    when fund_name ~ '부동산' then '부동산'
    when fund_name ~ '특별자산' then '특별자산'
    when fund_name ~ '혼합자산' then '혼합자산'
    when fund_name ~ '[\\[(]\\s*주식' or fund_name ~ '주식\\s*[\\])]' then '주식형'
    when fund_name ~ '[\\[(]\\s*채권' or fund_name ~ '채권\\s*[\\])]' then '채권형'
    end
where asset_sub is null
"""

FIELDS = ["fund_code", "legal_form", "asset_class", "asset_sub", "open_end", "additional", "special",
          "risk_grade", "risk_label", "offer_units", "trustee", "admin_co"]


def r4(x):
    return None if x is None else round(float(x), 4)


def refresh_funds(con, keys: set | None):
    con.execute(AGG_SQL)
    q = """select fund_key, rcept_no, kind, parsed from filings
           where parsed is not null {} order by fund_key, rcept_dt, rcept_no"""
    rows = (con.execute(q.format("and fund_key = any(%s)"), (list(keys),)).fetchall() if keys
            else con.execute(q.format("")).fetchall())
    merged: dict[str, dict] = {}
    for key, rcp, kind, p in rows:
        m = merged.setdefault(key, {})
        for f in FIELDS:
            v = p.get(f)
            if f in ("trustee", "admin_co"):
                v = company(v)
            if v not in (None, "", []):
                m[f] = v
        if kind == "ISSUE" and p.get("nav") is not None:
            for f in ("nav", "nav_prev", "subs_amt", "redeem_amt", "nav_period"):
                m[f] = p.get(f)
            m["nav_rcept_no"] = rcp
        if p.get("classes"):
            m["classes"] = p["classes"]
    out = []
    for key, m in merged.items():
        cl = m.get("classes") or []
        rep = cl[0] if cl else {}
        totals = [c["total"] for c in cl if c.get("total") is not None]
        out.append((m.get("fund_code"), m.get("legal_form"), m.get("asset_class"), m.get("asset_sub"),
                    m.get("open_end"), m.get("additional"), m.get("special"), m.get("risk_grade"),
                    m.get("risk_label"), m.get("offer_units"), m.get("trustee"), m.get("admin_co"),
                    r4(rep.get("mgmt")), r4(rep.get("sales")), r4(rep.get("trust")), r4(rep.get("admin")),
                    r4(rep.get("total")), r4(rep.get("ter")), r4(min(totals)) if totals else None,
                    r4(max(totals)) if totals else None, len(cl) or None, Jsonb(cl) if cl else None,
                    m.get("nav"), m.get("nav_prev"), m.get("subs_amt"), m.get("redeem_amt"),
                    m.get("nav_period"), m.get("nav_rcept_no"), key))
    with con.cursor() as cur:
        cur.executemany("""update funds set fund_code=%s, legal_form=%s, asset_class=%s, asset_sub=%s,
              open_end=%s, additional=%s, special=%s, risk_grade=%s, risk_label=%s, offer_units=%s,
              trustee=%s, admin_co=%s, fee_mgmt=%s, fee_sales=%s, fee_trust=%s, fee_admin=%s,
              fee_total=%s, fee_ter=%s, fee_total_min=%s, fee_total_max=%s, class_count=%s, classes=%s,
              nav=%s, nav_prev=%s, subs_amt=%s, redeem_amt=%s, nav_period=%s, nav_rcept_no=%s,
              updated_at=now() where fund_key=%s""", out)
    con.execute(NAME_SUB_SQL)
    print(f"[D] 펀드 {len(out)}개 원문 값 반영")


def probe_only():
    """DB 없이 최근 공시 몇 건을 파싱해 결과·원문을 DEBUG_DIR에 저장 (파서 점검용)"""
    dart, viewer = Dart(secret_key(), 50), Viewer(pause=0.5)
    end = date.today()
    picks = {"SHELF": [], "REG": [], "ISSUE": [], "PROSPECTUS": []}
    for ty in ("G001", "G002"):
        j = dart.json("list", pblntf_detail_ty=ty, bgn_de=f"{end - timedelta(days=60):%Y%m%d}",
                      end_de=f"{end:%Y%m%d}", page_count=100)
        for r in j.get("list", []):
            kind, amend, name = classify(r["report_nm"])
            if kind in picks and (not amend or kind == "PROSPECTUS") and len(picks[kind]) < (8 if kind in ("SHELF", "REG") else 3):
                picks[kind].append((r["rcept_no"], kind, amend, r["report_nm"]))
    os.makedirs(DEBUG_DIR or "debug", exist_ok=True)
    summary = []
    for rows in picks.values():
        for rcp, kind, amend, nm in rows:
            dbg = {}
            try:
                p = parse_filing(viewer, rcp, kind, amend, dbg)
            except Exception as e:  # noqa: BLE001
                p = {"error": repr(e)}
            summary.append({"rcept_no": rcp, "report_nm": nm, "parsed": p})
            with open(f"{DEBUG_DIR or 'debug'}/{rcp}_{kind}.json", "w", encoding="utf-8") as f:
                json.dump({"report_nm": nm, "parsed": p, "nodes": dbg.get("nodes"),
                           "html": [[t, h[:200000]] for t, h in dbg.get("html", [])]}, f, ensure_ascii=False)
            print(rcp, kind, nm[:60], "→", json.dumps({k: v for k, v in p.items() if k != "classes"},
                                                      ensure_ascii=False)[:400],
                  "| classes", len(p.get("classes") or []), (p.get("classes") or [None])[0])
    with open(f"{DEBUG_DIR or 'debug'}/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)


def main():
    if os.environ.get("PROBE_ONLY"):
        return probe_only()
    budget = int(os.environ.get("DART_BUDGET", "15000"))
    dart = Dart(secret_key(), budget)
    viewer = Viewer(pause=float(os.environ.get("WEB_PAUSE", "0.3")))
    with psycopg.connect(**db_kwargs(), autocommit=True, prepare_threshold=None) as con:
        print(f"[A] 공시 목록 수집 (최근 {YEARS}년, DART 호출 예산 {budget})")
        try:
            collect_list(con, dart)
        except BudgetExceeded as e:
            print("  ※", e)
        print(f"  DART 호출 {dart.calls}회")
        n = con.execute(TARGET_SQL).rowcount
        n += con.execute("""update filings set status = 'pending'
                            where status = 'error' and parsed_at < now() - interval '6 hours'""").rowcount
        n += con.execute("""update filings set status = 'pending'
                            where status = 'done' and kind in ('SHELF','REG') and not is_amend
                              and coalesce((parsed->>'_v')::int, 1) < %s""", (PARSER_VERSION,)).rowcount
        print(f"[B] 새 파싱 대상 {n}건")
        touched = parse_pending(con, viewer)
        refresh_funds(con, None)          # 매번 전체 재계산 (중간에 끊긴 실행이 남긴 누락도 메움)
        if not touched:
            con.execute(AGG_SQL)
        s = con.execute("""select (select count(*) from managers), (select count(*) from funds),
                                  (select count(*) from filings),
                                  (select count(*) from filings where status='pending'),
                                  (select count(*) from filings where status='done'),
                                  (select count(*) from filings where status='error')""").fetchone()
        print(f"※ 운용사 {s[0]} · 펀드 {s[1]} · 공시 {s[2]} (파싱 대기 {s[3]} · 완료 {s[4]} · 실패 {s[5]}) "
              f"· {minutes():.0f}분")


if __name__ == "__main__":
    main()

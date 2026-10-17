"""DART 웹 뷰어로 펀드 공시 원문을 읽고 필요한 값을 뽑는다.

OpenAPI document.xml은 일괄신고서·증권발행실적보고서 원문을 주지 않으므로(014),
공시 화면(dsaf001/main.do)의 목차 노드를 읽어 노드별 본문(report/viewer.do)을 받는다.
  - 상위 노드는 하위 노드 본문을 포함하므로 필요한 노드만 골라 받는다.
  - 정정 신고서의 본문은 PDF라 표지·요약정보만 읽을 수 있다.
"""
import html as htmlmod
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

WEB = "https://dart.fss.or.kr"
PARSER_VERSION = 2          # 원본 신고서 파싱 규칙을 바꾸면 올림 → 이전 결과는 다시 파싱


class Blocked(Exception):
    """DART 웹이 연속으로 응답하지 않음 → 이번 실행 중단"""


class Viewer:
    def __init__(self, pause: float = 0.5):
        self.pause = pause
        self.calls = 0
        self.fails = 0
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (fundflow; KAIST class project)"

    def get(self, path: str, params: dict, timeout=60) -> str:
        for attempt in range(4):
            try:
                time.sleep(self.pause)
                self.calls += 1
                r = self.s.get(f"{WEB}/{path}", params=params, timeout=timeout)
                if r.status_code == 200 and r.text:
                    self.fails = 0
                    r.encoding = r.apparent_encoding if not r.encoding else r.encoding
                    return r.text
                raise requests.HTTPError(f"HTTP {r.status_code}")
            except requests.RequestException as e:
                wait = 5 * 2 ** attempt
                print(f"  ! 웹 {path} {type(e).__name__} {e} → {wait}초 뒤 재시도")
                time.sleep(wait)
                self.s.close()
                self.s = requests.Session()
                self.s.headers["User-Agent"] = "Mozilla/5.0 (fundflow; KAIST class project)"
        self.fails += 1
        if self.fails >= 3:
            raise Blocked("DART 웹 응답 없음")
        raise RuntimeError(f"웹 {path} 실패")

    def nodes(self, rcp: str) -> list[dict]:
        h = self.get("dsaf001/main.do", {"rcpNo": rcp})
        out, cur = [], {}
        for k, v in re.findall(r"node\d+\['(\w+)'\]\s*=\s*\"([^\"]*)\"", h):
            if k == "text" and cur:
                out.append(cur)
                cur = {}
            cur[k] = v
        if cur:
            out.append(cur)
        return [n for n in out if n.get("eleId") and n.get("dcmNo")]

    def body(self, n: dict) -> str:
        return self.get("report/viewer.do", {
            "rcpNo": n["rcpNo"], "dcmNo": n["dcmNo"], "eleId": n["eleId"],
            "offset": n["offset"], "length": n["length"], "dtd": n.get("dtd", "dart4.xsd")}, timeout=90)


# ── 노드 고르기 ─────────────────────────────────────────────────────────
def squash(s: str) -> str:
    return re.sub(r"\s+", "", htmlmod.unescape(s or ""))


ORIGINAL_WANT = ("투자결정시유의사항", "요약정보", "1.집합투자기구의명칭", "2.집합투자기구의종류및형태",
                 "10.집합투자기구의투자위험", "13.보수및수수료", "3.기타집합투자기구관련회사")


def pick_nodes(nodes: list[dict], original: bool) -> list[dict]:
    """원본 신고서: 표지와 필요한 절만 / 그 밖의 공시: 짧은 노드 전부(최대 8개)"""
    if not nodes:
        return []
    if not original:
        return [n for n in nodes if int(n.get("length") or 0) <= 60000][:8]
    out, seen = [nodes[0]], set()
    for n in nodes[1:]:
        t = squash(n.get("text"))
        for w in ORIGINAL_WANT:
            if t.startswith(w) and w not in seen:
                seen.add(w)
                out.append(n)
    return out


# ── 텍스트 추출 ─────────────────────────────────────────────────────────
def plain(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def amount(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.replace(",", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


RISK_LABELS = ["매우 높은 위험", "높은 위험", "다소 높은 위험", "보통 위험", "낮은 위험", "매우 낮은 위험"]
ASSET_CLASSES = ["혼합자산", "특별자산", "부동산", "단기금융", "증권"]
ASSET_SUBS = ["혼합주식형", "혼합채권형", "주식형", "채권형", "재간접형", "파생형", "혼합형", "MMF"]
SPECIALS = ["모자형", "종류형", "상장지수", "전환형", "엄브렐러", "자펀드", "모펀드", "부동산투자", "환매금지형"]


def risk(t: str) -> tuple[Optional[int], Optional[str]]:
    for pat in (r"투자위험\s*등급\s*[:：]?\s*([1-6])\s*등급",
                r"6\s*등급\s*중\s*([1-6])\s*등급",
                r"위험등급_\s*([1-6])\s*등급",
                r"([1-6])\s*등급\s*[\[(（]\s*(?:매우\s*)?(?:다소\s*)?(?:높은|낮은|보통)\s*위험"):
        m = re.search(pat, t)
        if m:
            g = int(m.group(1))
            return g, RISK_LABELS[g - 1]
    return None, None


def fund_code(t: str) -> Optional[str]:
    m = re.search(r"펀드\s*코드\s*[:：]\s*([0-9A-Z]{5})\b", t)
    if m:
        return m.group(1)
    m = re.search(r"펀드코드\s*\(종류형\s*포함\)(.{0,600})", t)
    if m:
        for tok in re.findall(r"(?<![0-9A-Za-z])([0-9A-Z]{5})(?![0-9A-Za-z])", m.group(1)):
            if re.search(r"\d", tok):
                return tok
    return None


def fund_type(t: str) -> dict:
    out = {}
    m = re.search(r"형태별\s*종류\s*[:：]?\s*(투자신탁|투자회사|투자유한회사|투자합자회사|투자유한책임회사|투자합자조합|투자익명조합|사모투자전문회사)", t)
    if m:
        out["legal_form"] = m.group(1)
    m = re.search(r"운용자산별\s*종류\s*[:：]?\s*(.{1,40}?)\s*(?:다\.|개방형)", t)
    if m:
        s = m.group(1)
        out["asset_raw"] = s.strip()
        for c in ASSET_CLASSES:
            if c in s:
                out["asset_class"] = c
                break
        for c in ASSET_SUBS:
            if c in s:
                out["asset_sub"] = c
                break
    m = re.search(r"개방형\s*[ㆍ·・,]?\s*폐쇄형\s*구분\s*[:：]?\s*(개방형|폐쇄형)", t)
    if m:
        out["open_end"] = m.group(1) == "개방형"
    m = re.search(r"추가형\s*[ㆍ·・,]?\s*단위형\s*구분\s*[:：]?\s*(추가형|단위형)", t)
    if m:
        out["additional"] = m.group(1) == "추가형"
    m = re.search(r"특수형태\s*표시\s*[:：]?\s*(.{0,60}?)\s*(?:바\.|주1\)|3\.|$)", t)
    if m:
        sp = [s for s in SPECIALS if s in m.group(1)]
        if sp:
            out["special"] = sp
    return out


def issue_report(t: str) -> dict:
    """증권발행실적보고서: 대상기간의 설정·해지 금액과 기초·기말 순자산"""
    out = {}
    pats = {"subs_amt": r"설정\s*금액\s+(-?[\d,]+)", "redeem_amt": r"해지\s*금액\s+(-?[\d,]+)",
            "nav_prev": r"기초\s*순자산\s+(-?[\d,]+)", "nav": r"기말\s*순자산\s+(-?[\d,]+)"}
    for k, p in pats.items():
        m = re.search(p, t)
        if m:
            out[k] = amount(m.group(1))
    m = re.search(r"대상\s*기간\s*[:：]?\s*(\d{4}[.\-]\s*\d{1,2}[.\-]\s*\d{1,2})\s*~\s*(\d{4}[.\-]\s*\d{1,2}[.\-]\s*\d{1,2})", t)
    if m:
        out["nav_period"] = f"{m.group(1).replace(' ', '')} ~ {m.group(2).replace(' ', '')}"
    return out


# 수탁·사무관리 회사 이름 통일
ALIASES = [("국민은행", "KB국민은행"), ("농협은행", "NH농협은행"), ("기업은행", "IBK기업은행"),
           ("중소기업은행", "IBK기업은행"), ("스탠다드차타드", "SC제일은행"), ("SC제일", "SC제일은행"),
           ("홍콩상하이", "HSBC은행"), ("HSBC", "HSBC은행"), ("씨티은행", "한국씨티은행"),
           ("신한금융투자", "신한투자증권"), ("도이치", "도이치은행"), ("한국증권금융", "한국증권금융"),
           ("하나은행", "하나은행"), ("신한은행", "신한은행"), ("우리은행", "우리은행"),
           ("부산은행", "BNK부산은행"), ("경남은행", "BNK경남은행"), ("대구은행", "iM뱅크"),
           ("아이엠뱅크", "iM뱅크"), ("iM뱅크", "iM뱅크"), ("산업은행", "KDB산업은행"),
           ("수협은행", "Sh수협은행"), ("광주은행", "광주은행"), ("전북은행", "전북은행"),
           ("제이피모간", "JP모간체이스은행"), ("JP모간", "JP모간체이스은행")]


def company(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = re.sub(r"\(주\)|㈜|주식회사|\(株\)", "", name)
    s = re.sub(r"\s+", "", s).strip(" .,:;-")
    if not s or len(s) > 30 or len(s) < 2 or re.match(r"(주소|연락|대표|홈페이지|전화|해당|없음|-)", s):
        return None
    for k, v in ALIASES:
        if k in s:
            return v
    return s


NAME_AFTER = re.compile(r"회\s*사\s*명\s*[:：]?\s*(.{2,40}?)\s*(?:주\s*소|소\s*재\s*지|본\s*점|연\s*락\s*처|대\s*표|홈\s*페\s*이\s*지|설\s*립|전\s*화|\(|$)")


def _name_at(t: str) -> Optional[str]:
    m = NAME_AFTER.search(t)
    if not m:
        return None
    name = company(m.group(1))
    if name and re.search(r"평가|회계|법무", name):
        return None
    return name


def _company_after(t: str, head: str, window: int = 300) -> Optional[str]:
    i = t.find(head)
    if i < 0:
        return None
    return _name_at(t[i:i + window])


def related_companies(t: str, section: Optional[str] = None) -> dict:
    """section = '기타 집합투자기구 관련회사' 절 본문: 첫 회사명이 신탁업자(보관회사), 일반사무관리회사 절의 회사명이 사무관리"""
    out = {}
    src = section or t
    tr = None
    if section:
        tr = (_company_after(section, "신탁업자", 400) or _company_after(section, "신탁회사", 400)
              or _company_after(section, "보관회사", 400) or _name_at(section[:600]))
    else:
        tr = (_company_after(t, "[가] 신탁") or _company_after(t, "신탁회사에 관한") or _company_after(t, "신탁업자에 관한"))
    if tr:
        out["trustee"] = tr
    ad = None
    m = re.search(r"(?:[가-하]\.|\]|\d\.|\))\s*일반사무관리회사(?!는|가|의|,|및)", src)
    if m:
        ad = _name_at(src[m.start():m.start() + 400])
    if ad and ad != tr:
        out["admin_co"] = ad
    return out


# ── 보수 표 ─────────────────────────────────────────────────────────────
def grid(tbl) -> list[list[str]]:
    """rowspan·colspan을 펼친 2차원 표"""
    rows, carry = [], {}
    for tr in tbl.find_all("tr"):
        row, col = [], 0
        cells = tr.find_all(["td", "th"])
        ci = 0
        while ci < len(cells) or col in carry:
            if col in carry:
                txt, left = carry[col]
                row.append(txt)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (txt, left - 1)
                col += 1
                continue
            c = cells[ci]
            ci += 1
            txt = re.sub(r"\s+", " ", c.get_text(" ", strip=True))
            cs = int(re.sub(r"\D", "", c.get("colspan", "1")) or 1)
            rs = int(re.sub(r"\D", "", c.get("rowspan", "1")) or 1)
            for _ in range(cs):
                row.append(txt)
                if rs > 1:
                    carry[col] = (txt, rs - 1)
                col += 1
        rows.append(row)
    return rows


NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def _num(s: str) -> Optional[float]:
    s = (s or "").replace("%", "").replace(",", "").strip()
    m = re.match(r"^\(?\s*(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _field(label: str) -> Optional[str]:
    k = re.sub(r"[\sㆍ·・,]", "", label)
    if "동종" in k:
        return "similar"
    if "총보수" in k and "비용" in k:
        return "ter_fof" if "피투자" in k else "ter"
    if "집합투자업자" in k or "운용보수" in k or "운용회사" in k:
        return "mgmt"
    if "판매회사" in k or "판매보수" in k:
        return "sales"
    if "수탁" in k or "신탁업자" in k:
        return "trust"
    if "일반사무" in k or "사무관리" in k:
        return "admin"
    if k.startswith("기타비용") or k.endswith("기타비용"):
        return "other"
    if "거래비용" in k:
        return "trade"
    if "총보수" in k or k.endswith("합계"):
        return "total"
    return None


def fee_table(h: str) -> Optional[dict]:
    """'집합투자기구에 부과되는 보수 및 비용' 표 → 클래스별 연 보수(%)"""
    soup = BeautifulSoup(h, "lxml")
    for tbl in soup.find_all("table"):
        g = [r for r in grid(tbl) if any(c.strip() for c in r)]
        if len(g) < 2:
            continue
        flat = squash(" ".join(" ".join(r) for r in g[:4]))
        if "판매회사" not in flat or "보수" not in flat or ("집합투자업자" not in flat and "운용" not in flat):
            continue
        # 세로형(첫 열이 항목명)이면 뒤집는다
        first_col = [r[0] for r in g if r]
        if sum(1 for c in first_col if _field(c) in ("mgmt", "sales", "trust")) >= 2:
            width = max(len(r) for r in g)
            g = [[(r[i] if i < len(r) else "") for r in g] for i in range(width)]
        # 머리글 행 = 숫자 칸이 3개 미만인 앞쪽 행들
        hdr = 0
        while hdr < len(g) and sum(1 for c in g[hdr] if NUM.match(c.replace(",", "").strip())) < 3:
            hdr += 1
        if hdr == 0 or hdr >= len(g):
            continue
        width = max(len(r) for r in g)
        labels = []
        for i in range(width):
            parts = []
            for r in g[:hdr]:
                v = r[i] if i < len(r) else ""
                if v and v not in parts:
                    parts.append(v)
            labels.append(" ".join(parts))
        cols = {}
        for i, lab in enumerate(labels):
            f = _field(lab)
            if f and f not in cols:
                cols[f] = i
        if not {"mgmt", "sales"} <= set(cols) and "total" not in cols:
            continue
        classes = []
        for r in g[hdr:]:
            nums = [c for c in r if NUM.match(c.replace(",", "").strip())]
            if len(nums) < 3:
                if classes:
                    break
                continue
            name = next((c for c in r if c and not NUM.match(c.replace(",", "").strip())), "") or "-"
            row = {"class": name[:40]}
            for f, i in cols.items():
                if f in ("mgmt", "sales", "trust", "admin", "total", "ter", "ter_fof", "other"):
                    row[f] = _num(r[i]) if i < len(r) else None
            if row.get("total") is None:
                parts = [row.get(k) for k in ("mgmt", "sales", "trust", "admin")]
                if all(p is not None for p in parts[:2]):
                    row["total"] = round(sum(p or 0 for p in parts), 4)
            if row.get("ter") is None and row.get("ter_fof") is not None:
                row["ter"] = row["ter_fof"]
            row.pop("ter_fof", None)
            # 비율이 아닌 값(설명 문구의 숫자 등)이면 버림
            if any((row.get(k) or 0) > 10 for k in ("mgmt", "sales", "total", "ter")):
                continue
            classes.append(row)
        if classes:
            return {"classes": classes[:40]}
    return None


# ── 공시 1건 파싱 ───────────────────────────────────────────────────────
def parse_filing(v: Viewer, rcp: str, kind: str, is_amend: bool, debug: Optional[dict] = None) -> dict:
    nodes = v.nodes(rcp)
    original = kind in ("SHELF", "REG") and not is_amend
    picked = pick_nodes(nodes, original)
    htmls = []
    for n in picked:
        htmls.append((squash(n.get("text")), v.body(n)))
    if debug is not None:
        debug["nodes"] = [n.get("text") for n in nodes]
        debug["html"] = htmls
    text_all = " ".join(plain(h) for _, h in htmls)
    out: dict = {}
    g, lab = risk(text_all)
    if g:
        out["risk_grade"], out["risk_label"] = g, lab
    c = fund_code(text_all)
    if c:
        out["fund_code"] = c
    out.update(fund_type(text_all))
    m = re.search(r"모집\s*\(?매출\)?\s*증권의\s*종류\s*및\s*수\s*[:：]?\s*(.{5,120}?)(?:\s*\[|\s*7\.)", text_all)
    if m:
        out["offer_units"] = m.group(1).strip()
    if kind == "ISSUE":
        out.update(issue_report(text_all))
    if original:
        sec = " ".join(plain(h) for t, h in htmls if t.startswith("3.기타집합투자기구관련회사"))
        out.update(related_companies(text_all, sec or None))
        fee_html = [h for t, h in htmls if t.startswith("13.보수")] or [h for t, h in htmls if t.startswith("요약정보")]
        for h in fee_html:
            ft = fee_table(h)
            if ft:
                out.update(ft)
                break
    out["_nodes"] = len(nodes)
    out["_v"] = PARSER_VERSION
    return out

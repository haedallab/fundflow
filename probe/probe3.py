"""첨부문서(간이투자설명서 등)와 일괄신고서의 웹 뷰어 본문 확인"""
import json, os, re, time, html
import requests
OUT = "samples3"; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (fundflow research)"
KW = ["신탁업자", "일반사무관리회사", "판매회사보수", "총보수", "집합투자업자보수", "운용보수", "운용자산별", "투자위험등급", "펀드코드", "종류형", "모집예정금액", "판매회사"]
def plain(t): return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
def main_page(rcp, dcm=None):
    p = {"rcpNo": rcp}
    if dcm: p["dcmNo"] = dcm
    return S.get("https://dart.fss.or.kr/dsaf001/main.do", params=p, timeout=60).text
def nodes(h):
    out, cur = [], {}
    for k, v in re.findall(r"node\d+\['(\w+)'\]\s*=\s*\"([^\"]*)\"", h):
        if k == "text" and cur: out.append(cur); cur = {}
        cur[k] = v
    if cur: out.append(cur)
    return out
def atts(h):
    sel = re.search(r'<select id="att".*?</select>', h, re.S)
    return [(v, re.sub(r"\s+", " ", t).strip()) for v, t in re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', sel.group(0) if sel else "", re.S) if v != "null"]
def view(n):
    r = S.get("https://dart.fss.or.kr/report/viewer.do", params={k: n[k] for k in ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd")}, timeout=90)
    return r.text
log = {}
for rcp in ["20261014000012", "20261014000003", "20260929000570", "20261008000267", "20261013000322"]:
    h = main_page(rcp); a = atts(h); ns = nodes(h)
    entry = {"nodes": [n.get("text") for n in ns], "atts": a}
    full = "".join(plain(view(n)) for n in ns[:40]); time.sleep(0.3)
    entry["main_len"] = len(full); entry["main_kw"] = [w for w in KW if w in full]
    open(f"{OUT}/{rcp}_main.txt", "w", encoding="utf-8").write(full)
    # 같은 접수번호의 첨부만
    for v, t in a:
        m = re.search(r"rcpNo=(\d+)&(?:amp;)?dcmNo=(\d+)", v)
        if not m or m.group(1) != rcp: continue
        h2 = main_page(rcp, m.group(2)); ns2 = nodes(h2)
        txt = "".join(plain(view(n)) for n in ns2[:80]); time.sleep(0.3)
        open(f"{OUT}/{rcp}_att_{m.group(2)}.txt", "w", encoding="utf-8").write(txt)
        entry.setdefault("att_read", []).append({"title": t, "dcm": m.group(2), "nodes": [n.get("text") for n in ns2][:60], "len": len(txt), "kw": [w for w in KW if w in txt]})
    log[rcp] = entry
    print(rcp, json.dumps({k: v for k, v in entry.items() if k != "atts"}, ensure_ascii=False)[:1500])
json.dump(log, open(f"{OUT}/log.json", "w"), ensure_ascii=False, indent=1)

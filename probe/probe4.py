"""웹 뷰어: 문서 전체를 offset/length 한 번에 받을 수 있는지 확인"""
import json, os, re, html, time
import requests
OUT = "samples4"; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (fundflow research)"
def plain(t): return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
def nodes(h):
    out, cur = [], {}
    for k, v in re.findall(r"node\d+\['(\w+)'\]\s*=\s*\"([^\"]*)\"", h):
        if k == "text" and cur: out.append(cur); cur = {}
        cur[k] = v
    if cur: out.append(cur)
    return out
def view(rcp, dcm, ele, off, ln):
    return S.get("https://dart.fss.or.kr/report/viewer.do", params={"rcpNo": rcp, "dcmNo": dcm, "eleId": ele, "offset": off, "length": ln, "dtd": "dart4.xsd"}, timeout=90).text
for rcp in ["20261013000322", "20260929000570", "20261014000003"]:
    h = S.get("https://dart.fss.or.kr/dsaf001/main.do", params={"rcpNo": rcp}, timeout=60).text
    ns = nodes(h)
    print(rcp, [(n.get("text"), n.get("eleId"), n.get("offset"), n.get("length")) for n in ns][:40])
    sep = sum(len(plain(view(rcp, n["dcmNo"], n["eleId"], n["offset"], n["length"]))) for n in ns)
    o = min(int(n["offset"]) for n in ns); e = max(int(n["offset"]) + int(n["length"]) for n in ns)
    t1 = view(rcp, ns[0]["dcmNo"], ns[0]["eleId"], o, e - o)
    t2 = view(rcp, ns[0]["dcmNo"], "0", o, e - o)
    print(" separate", sep, "| combined ele=first", len(plain(t1)), "| combined ele=0", len(plain(t2)))
    open(f"{OUT}/{rcp}_combined.html", "w", encoding="utf-8").write(t1)
    time.sleep(0.5)

"""DART 펀드공시(G) 샘플 수집: 목록 통계 + 신고서 원문 몇 건 저장"""
import io, json, os, sys, time, zipfile, collections
from datetime import date, timedelta
import requests

KEY = os.environ.get("DART_API_KEY", "").strip()
print("key length:", len(KEY))
if len(KEY) != 40:
    raise SystemExit("DART_API_KEY 시크릿이 없거나 40자가 아닙니다")
OUT = "samples"
os.makedirs(OUT, exist_ok=True)
S = requests.Session()

def get(path, **p):
    for i in range(5):
        try:
            r = S.get(f"https://opendart.fss.or.kr/api/{path}", params={"crtfc_key": KEY, **p}, timeout=60)
            r.raise_for_status()
            return r
        except Exception as e:
            print("retry", path, e); time.sleep(5 * (i + 1))
    raise SystemExit(f"fail {path}")

end = date.today(); bgn = end - timedelta(days=60)
rows = []
for dt in ("G001", "G002", "G003"):
    page = 1
    while True:
        j = get("list.json", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                pblntf_ty="G", pblntf_detail_ty=dt, page_no=page, page_count=100).json()
        print(dt, page, j.get("status"), j.get("message"), j.get("total_count"))
        if j.get("status") != "000": break
        for r in j["list"]: r["_detail"] = dt
        rows += j["list"]
        if page >= int(j.get("total_page", 1)) or page >= 5: break
        page += 1; time.sleep(0.3)
json.dump(rows, open(f"{OUT}/list_60d.json", "w"), ensure_ascii=False, indent=1)
# 연간 건수 (3년, 월별)
counts = {}
for y in range(3):
    e = end - timedelta(days=365 * y); b = e - timedelta(days=89)
    j = get("list.json", bgn_de=b.strftime("%Y%m%d"), end_de=e.strftime("%Y%m%d"), pblntf_ty="G", page_count=1).json()
    counts[f"{b}~{e}"] = j.get("total_count")
json.dump(counts, open(f"{OUT}/counts.json", "w"), ensure_ascii=False, indent=1)
print(collections.Counter((r["_detail"], r["report_nm"].split("(")[0][:30]) for r in rows).most_common(40))
print(collections.Counter(r["corp_name"] for r in rows).most_common(20))
print(collections.Counter(r["flr_nm"] for r in rows).most_common(20))

# 샘플 원문: 유형별 + 정정 1건 + 투자회사 1건
picks, seen = [], set()
for r in rows:
    k = (r["_detail"], "정정" in r["report_nm"], "투자설명서" in r["report_nm"])
    if k in seen: continue
    seen.add(k); picks.append(r)
for r in picks[:10]:
    d = f"{OUT}/{r['rcept_no']}"
    os.makedirs(d, exist_ok=True)
    json.dump(r, open(f"{d}/meta.json", "w"), ensure_ascii=False, indent=1)
    b = get("document.xml", rcept_no=r["rcept_no"]).content
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        for n in z.namelist():
            open(f"{d}/{n}", "wb").write(z.read(n))
        print("saved", r["rcept_no"], r["report_nm"], z.namelist())
    except zipfile.BadZipFile:
        open(f"{d}/error.txt", "wb").write(b[:2000])
    time.sleep(0.5)

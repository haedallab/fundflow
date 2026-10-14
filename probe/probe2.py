"""원문 구조 2차 확인: 유형별 document.xml 내용 + DART 웹 뷰어 본문"""
import io, json, os, re, time, zipfile, collections
from datetime import date, timedelta
import requests

_raw = os.environ.get("DART_API_KEY", "")
KEY = re.findall(r"[0-9a-fA-F]{40}", _raw)[0]
OUT = "samples2"; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (fundflow research)"
KW = ["신탁업자", "일반사무관리회사", "판매회사보수", "총보수", "집합투자업자보수", "운용자산별", "증권(주식형)", "투자위험등급", "펀드코드", "종류형"]

def api(path, **p):
    return S.get(f"https://opendart.fss.or.kr/api/{path}", params={"crtfc_key": KEY, **p}, timeout=90)

end = date.today(); bgn = end - timedelta(days=30)
rows = []
for page in (1, 2, 3):
    j = api("list.json", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), pblntf_ty="G", page_no=page, page_count=100).json()
    rows += j.get("list", [])
# detail type filter test
for dt in ("G001", "G002", "G003"):
    j = api("list.json", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), pblntf_detail_ty=dt, page_count=1).json()
    print("detail-only", dt, j.get("status"), j.get("total_count"))
kinds = collections.defaultdict(list)
for r in rows:
    k = re.sub(r"\(.*", "", r["report_nm"]).replace("[기재정정]", "정정:")
    kinds[k].append(r)
print({k: len(v) for k, v in kinds.items()})
log = {}
for k, lst in kinds.items():
    for r in lst[:2]:
        rn = r["rcept_no"]; b = api("document.xml", rcept_no=rn).content
        info = {"report_nm": r["report_nm"], "bytes": len(b)}
        try:
            z = zipfile.ZipFile(io.BytesIO(b)); files = {}
            for n in z.namelist():
                t = z.read(n).decode("utf-8", "ignore"); plain = re.sub(r"<[^>]+>", " ", t)
                files[n] = {"len": len(t), "kw": [w for w in KW if w in plain]}
                if k in ("투자설명서", "증권신고서", "정정:투자설명서", "일괄신고서", "정정:일괄신고서", "정정:증권신고서", "증권발행실적보고서"):
                    open(f"{OUT}/{rn}_{n}", "w", encoding="utf-8").write(t)
            info["files"] = files
        except zipfile.BadZipFile:
            info["error"] = b[:200].decode("utf-8", "ignore")
        log[rn] = info; time.sleep(0.4)
json.dump(log, open(f"{OUT}/doc_log.json", "w"), ensure_ascii=False, indent=1)

# DART 웹 뷰어 (투자설명서 1건)
tgt = next((r for r in rows if r["report_nm"].startswith("투자설명서") or "]투자설명서" in r["report_nm"]), rows[0])
rn = tgt["rcept_no"]
h = S.get("https://dart.fss.or.kr/dsaf001/main.do", params={"rcpNo": rn}, timeout=60).text
open(f"{OUT}/web_main_{rn}.html", "w", encoding="utf-8").write(h)
dcm = re.findall(r"dcmNo['\"]?\]?\s*=\s*['\"](\d+)", h) or re.findall(r"dcmNo=(\d+)", h)
print("dcmNo candidates", dcm[:10])
nodes = re.findall(r"node\d+\['(\w+)'\]\s*=\s*\"([^\"]*)\"", h)
open(f"{OUT}/web_nodes_{rn}.json", "w", encoding="utf-8").write(json.dumps(nodes[:400], ensure_ascii=False, indent=0))
att = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', h)
print("attachments", att[:20])
if dcm:
    for ele in ("0", "1"):
        v = S.get("https://dart.fss.or.kr/report/viewer.do", params={"rcpNo": rn, "dcmNo": dcm[0], "eleId": ele, "offset": "0", "length": "0", "dtd": "dart4.xsd"}, timeout=90)
        open(f"{OUT}/web_viewer_{rn}_{ele}.html", "w", encoding="utf-8").write(v.text)
        plain = re.sub(r"<[^>]+>", " ", v.text)
        print("viewer eleId", ele, v.status_code, len(v.text), [w for w in KW if w in plain])

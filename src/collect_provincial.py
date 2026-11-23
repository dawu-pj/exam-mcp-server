r"""方案 B 定向采集：省级考试院「差异化考务公告」→ 临时目录（2026-11-24）。

依据：《受控扩量调查与实施方案》第五节。调查实证：加 12 篇研招网教育部政策
后 doc_hit@1 87%→67%（主题同质 → IDF 稀释）；故本脚本**刻意不抓**研招网政策，
只抓上海/广东/浙江/北京考试院的本省考务类公告（报名、考务、成绩、准考证等），
与现有 18 篇国家级公告主题不同质。

合规：公开静态页、明示 UA（与 crawler/settings.py 一致）、每域 1 req/s、
      只 GET、robots 404 的站按未声明限制但从严限速、WAF 特征即停、不绕登录、
      不写 data/clean（全部落 data/expansion_candidates/ 临时目录）。

入库前四道过滤：
  1. 按站定制文章 URL 模式（通用正则会把 /index.html 导航页当文章）
  2. 纯导航标题剔除 + 国家级政策同质主题剔除（教育部/管理规定/全国硕士研究生…）
  3. 正文 >= 500 字（实测 308 字窗口页是假正文）
  4. PII 脱敏 + 正文指纹去重（与 data/clean 同口径，含跨站重复）

用法：
  .venv/Scripts/python.exe src/collect_provincial.py
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "crawler"))
from cleaning import content_fingerprint, mask_pii, normalize_url  # noqa: E402

OUT_DIR = ROOT / "data" / "expansion_candidates"
CLEAN_DIR = ROOT / "data" / "clean"
REPORT = OUT_DIR / "collect_report.json"
UA = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research; contact: local)"
WAF_SIGNS = ("acw_tc", "__jsluid_s", "WebShieldDRSessionVerify", "aliyungf_tc")
MIN_CHARS = 500
PARSER_VERSION = "trafilatura-2.0.0"
INDEX_VERSION = "v1"
MAX_PAGES = 3          # 每站最多看 3 个栏目页（首页 + 最多 2 个翻页）
SLEEP = 1.0            # 每域严格 1 req/s

# 按站定制文章 URL 模式 —— 不能复用 probe_columns.py 的通用正则
SITES = [
    {
        "name": "上海市教育考试院",
        "column": "https://www.shmeea.edu.cn/page/08000/",
        "article": re.compile(r"/page/08000/\d{8}/\d+\.html$"),
        "quota": 8,
        "note": "robots 显式 User-agent:*/Allow:/，合规最干净，优先",
    },
    {
        "name": "广东省教育考试院",
        "column": "https://eea.gd.gov.cn/tzgg/index.html",
        "article": re.compile(r"/tzgg/content/post_\d+\.html$"),
        "quota": 8,
        "note": "排除 /index.html 导航页",
    },
    {
        "name": "浙江省教育考试院",
        "column": "https://www.zjzs.net/col/col156/index.html",
        "article": re.compile(r"/(?:col\d+/)?art_[^/]+\.html$"),
        "quota": 7,
        "note": "col156 无斜杠地址是导航骨架页；文章列表在 /col/col156/index.html",
    },
    {
        "name": "北京教育考试院",
        "column": "https://www.bjeea.cn/html/bjeeagg/index.html",
        "article": re.compile(r"/html/(?:[a-z0-9]+/)*\d{4}/\d{1,2}/\d+\.html$"),
        "quota": 7,
        "note": "bjeeagg 无斜杠地址是导航页；文章路径可能多一层栏目段（如 /html/zkzz/tzgg/2026/1107/88452.html）",
    },
]

# 纯导航标题（调查发现 neea 三站各混 2 篇，新站同样有）
NAV_TITLE = re.compile(
    r"^(首页|更多|ENGLISH|关于我们|考试资讯|公示公告|通知公告|新闻资讯|考试动态|"
    r"要闻动态|报名报考|政务公开|考试招生|招考资讯|研考研招|高考高招|中考中招|"
    r"高中学考合格考|考试院简介|详细|返回|上一页|下一页|尾页)$"
)
# 国家级政策同质主题 —— 方案 B 的核心：刻意避开，防止重演 IDF 稀释
HOMOGENEOUS = re.compile(
    r"教育部|管理规定|全国硕士研究生|研究生招生工作|复试录取|退役士兵专项|"
    r"同等学力|双一流|教学〔|教研〔|部署20\d{2}年全国硕士"
)
# 正面主题：地方考务类公告（宽松匹配，避免误抓无关新闻）
LOCAL_EXAM = re.compile(
    r"考试|报名|报考|考务|成绩|准考证|考点|考场|资格审核|自考|学业水平|"
    r"学考|艺术|统考|机考|免考|毕业|招生|录取|评卷|补考|考前提示|时间安排"
)


def get(url, timeout=20):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.encoding = r.apparent_encoding or r.encoding
    return r


def waf_hit(resp):
    blob = (resp.headers.get("Set-Cookie", "") or "") + resp.text[:2000]
    return [s for s in WAF_SIGNS if s in blob]


def existing_state():
    """从 data/clean 的 sidecar 读已有 URL 与正文指纹，统一口径。"""
    urls, fps = set(), set()
    for p in CLEAN_DIR.glob("*.meta.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        urls.add(normalize_url(m["source_url"]))
        if m.get("content_sha256"):
            fps.add(m["content_sha256"])
    return urls, fps


def find_pages(site, column_html, column_url):
    """从栏目页挖同域翻页链接，最多 MAX_PAGES 个（含首页）。"""
    pages = [column_url]
    soup = BeautifulSoup(column_html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = urljoin(column_url, a["href"])
        if urlparse(href).netloc != urlparse(column_url).netloc:
            continue
        if re.search(r"index_\d+\.html$|list_\d+\.html$|page=\d+", href, re.I):
            found.append(href.split("#")[0])
    for href in sorted(set(found)):
        if href not in pages:
            pages.append(href)
        if len(pages) >= MAX_PAGES:
            break
    return pages


def extract_links(site, html, page_url):
    """按站定制模式提文章链接 + 标题，过滤导航/同质/非考务。"""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0]
        if urlparse(href).netloc != urlparse(page_url).netloc:
            continue
        if not site["article"].search(urlparse(href).path):
            continue
        if href in seen:
            continue
        title = (a.get("title") or "").strip() or a.get_text(strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 8 or NAV_TITLE.match(title):
            continue
        if not LOCAL_EXAM.search(title):
            continue
        if HOMOGENEOUS.search(title):
            continue
        seen.add(href)
        out.append({"url": href, "title_hint": title[:120]})
    return out


def title_of(text, hint):
    """与 ExtractPipeline 一致用 markdown 首行；首行不像标题时回退链接标题。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if 4 <= len(first) <= 80 and "。" not in first[:40]:
        return first[:120]
    return hint


def collect_site(site, existing_urls, existing_fps):
    entry = {"name": site["name"], "column": site["column"], "note": site["note"],
             "pages": [], "candidate_links": 0, "saved": [], "skipped": [], "waf": False}
    saved = []
    column_html = ""
    try:
        r = get(site["column"])
        entry["column_http"] = r.status_code
        if waf_hit(r):
            entry["waf"] = True
            entry["skipped"].append({"reason": "栏目页命中 WAF，即停"})
            return entry, saved
        if r.status_code != 200:
            entry["skipped"].append({"reason": f"栏目页 HTTP {r.status_code}"})
            return entry, saved
        column_html = r.text
        pages = find_pages(site, column_html, site["column"])
        time.sleep(SLEEP)
    except Exception as e:
        entry["skipped"].append({"reason": f"{type(e).__name__}: {str(e)[:120]}"})
        return entry, saved

    links = []
    for page in pages:
        entry["pages"].append(page)
        try:
            if page == site["column"]:
                html = column_html  # 首页已请求过，不重复打站
            else:
                page_resp = get(page)
                if waf_hit(page_resp):
                    entry["waf"] = True
                    entry["skipped"].append({"reason": f"翻页命中 WAF 即停: {page}"})
                    break
                if page_resp.status_code != 200:
                    entry["skipped"].append({"reason": f"翻页 HTTP {page_resp.status_code}: {page}"})
                    time.sleep(SLEEP)
                    continue
                html = page_resp.text
            for c in extract_links(site, html, page):
                if c["url"] not in [x["url"] for x in links]:
                    links.append(c)
            time.sleep(SLEEP)
        except Exception as e:
            entry["skipped"].append({"reason": f"翻页异常 {type(e).__name__}: {str(e)[:80]}"})
            time.sleep(SLEEP)
    entry["candidate_links"] = len(links)
    print(f"  [{site['name']}] 栏目页 {len(entry['pages'])} 个，候选链接 {len(links)} 条")

    for c in links:
        if len(saved) >= site["quota"]:
            break
        url = normalize_url(c["url"])
        if url in existing_urls or url in {s["source_url"] for s in saved}:
            entry["skipped"].append({"title": c["title_hint"][:40], "reason": "URL 已存在"})
            continue
        try:
            resp = get(c["url"])
            if waf_hit(resp):
                entry["waf"] = True
                entry["skipped"].append({"title": c["title_hint"][:40], "reason": "详情页命中 WAF，全站即停"})
                break
            if resp.status_code != 200:
                entry["skipped"].append({"title": c["title_hint"][:40], "reason": f"HTTP {resp.status_code}"})
                time.sleep(SLEEP)
                continue
            txt = trafilatura.extract(
                resp.text, include_comments=False, include_tables=False,
                favor_recall=True, url=c["url"],
            ) or ""
            if len(txt) < MIN_CHARS:
                entry["skipped"].append({"title": c["title_hint"][:40], "reason": f"正文仅 {len(txt)} 字"})
                time.sleep(SLEEP)
                continue
            clean_text, hits = mask_pii(txt)
            fp = content_fingerprint(clean_text)
            if fp in existing_fps or fp in {s["content_sha256"] for s in saved}:
                entry["skipped"].append({"title": c["title_hint"][:40], "reason": "正文指纹重复"})
                time.sleep(SLEEP)
                continue
            from hashlib import sha256
            doc_id = sha256(url.encode("utf-8")).hexdigest()[:16]
            meta = {
                "doc_id": doc_id,
                "source_url": url,
                "title": title_of(clean_text, c["title_hint"]),
                "markdown": clean_text,
                "site_name": site["name"],
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "http_status": resp.status_code,
                "parser_version": PARSER_VERSION,
                "index_version": INDEX_VERSION,
                "lang": "zh",
                "content_sha256": fp,
                "pii_redactions": len(hits),
                "text_chars": len(clean_text),
                "title_hint": c["title_hint"],
                "gate": "pending",  # 门禁未过，不得入库
            }
            (OUT_DIR / f"{doc_id}.md").write_text(clean_text, encoding="utf-8")
            (OUT_DIR / f"{doc_id}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            existing_fps.add(fp)
            saved.append(meta)
            print(f"    收录 {doc_id[:12]} | {len(clean_text)} 字 | {meta['title'][:36]}")
        except Exception as e:
            entry["skipped"].append({"title": c["title_hint"][:40], "reason": f"{type(e).__name__}: {str(e)[:80]}"})
        time.sleep(SLEEP)
    entry["saved"] = [{"doc_id": s["doc_id"], "title": s["title"], "text_chars": s["text_chars"]} for s in saved]
    return entry, saved


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # --only=浙江,北京：补抓指定站，不重复打已完成站点（限速礼貌）
    only = None
    for arg in sys.argv[1:]:
        if arg.startswith("--only="):
            only = [k.strip() for k in arg.split("=", 1)[1].split(",")]
    targets = [s for s in SITES if not only or any(k in s["name"] for k in only)]
    report_path = OUT_DIR / ("collect_report_batch2.json" if only else "collect_report.json")
    existing_urls, existing_fps = existing_state()
    print(f"现有库 {len(existing_urls)} 篇（URL/指纹基准）；本轮目标站 {len(targets)} 个")
    report = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "note": "方案B临时候选：未入库、未改主链路；门禁 doc_hit@1>=80% 才允许复制进 data/clean",
        "ua": UA, "min_chars": MIN_CHARS, "sleep_sec": SLEEP,
        "existing_docs": len(existing_urls), "sites": [],
    }
    all_saved = []
    for site in targets:
        print(f"\n=== {site['name']} ===")
        entry, saved = collect_site(site, existing_urls, existing_fps)
        report["sites"].append(entry)
        all_saved.extend(saved)
    report["saved_total"] = len(all_saved)
    report["skipped_total"] = sum(len(s["skipped"]) for s in report["sites"])
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计候选 {len(all_saved)} 篇 | 跳过 {report['skipped_total']} 条 | 留痕 {report_path}")
    print("注意：这些文档在临时目录，尚未入库；下一步跑门禁。")


if __name__ == "__main__":
    main()

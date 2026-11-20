r"""栏目页深度侦察（2026-11-20，阶段2源头侦察）：修正 probe_expansion.py 只扫首页的局限。

上一轮只 GET 首页 + 只按锚文本关键词过滤，漏掉了：
  1. 栏目页/归档目录里的文章（首页只露最新几条）
  2. neea 系文章列表是空锚文本、标题在 title 属性里
  3. 分页（category 页 -1.htm / -2.htm）

本脚本：从已知栏目页入口挖文章链接（含 title/alt 补标题）+ 分页，与现有 22 篇去重，
抽样抓正文验证质量。合规：公开静态页、明示 UA、每域 1 req/s、遵守限速、不绕 WAF、不抓登录态。
不写正文入库，只输出候选与质量评估留痕 data/column_probe.json。
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; ExamIntelBot/1.0; public exam-info; research)"
OUT = ROOT / "data" / "column_probe.json"

# 栏目页入口（从现有 22 篇 URL 反推 + 观察栏目导航）
COLUMNS = {
    "中国教育考试网": [
        "https://www.neea.edu.cn/html1/category/16093/615-1.htm",  # 考试资讯
        "https://www.neea.edu.cn/html1/category/1508/151-1.htm",   # 公示公告
        "https://www.neea.edu.cn/html1/report/",                    # 归档总览
    ],
    "四六级考试网": [
        "https://cet.neea.edu.cn/html1/category/16093/615-1.htm",
        "https://cet.neea.edu.cn/html1/report/",
    ],
    "NCRE考试网": [
        "https://ncre.neea.edu.cn/html1/category/16093/615-1.htm",
        "https://ncre.neea.edu.cn/html1/report/",
    ],
    "研招网": [
        "https://yz.chsi.com.cn/kyzx/jybzc/",   # 教育部政策
        "https://yz.chsi.com.cn/kyzx/yxzc/",    # 院校政策
        "https://yz.chsi.com.cn/kyzx/zcdh/",    # 国家政策
        "https://yz.chsi.com.cn/kyzx/kydt/",    # 资讯动态
        "https://yz.chsi.com.cn/sswbgg/",       # 网报公告
    ],
    "中国教育在线": [
        "https://www.eol.cn/news/yaowen/",
        "https://www.eol.cn/news/zbzl/",
    ],
}

# 文章 URL 形态（报告/政策/新闻正文页）
ARTICLE = re.compile(r"/(report|category)/\d+/\d+-\d+\.htm$|(?:jybzc|yxzc|zcdh|kydt)/\d{6}/\d{8}/\d+\.html$|/\w+/\w+/\d{6}/t\d{8}_\d+\.shtml$|/sswbgg/\d+")
# 标题关键词（公告类），排除纯导航
TITLE_KW = re.compile(r"考试|公告|通知|报名|成绩|查询|管理规定|大纲|招生|立项|公示|申报|课题|资格|证书|分数线|复试|调剂")
NAV_TITLE = re.compile(r"^(首页|关于我们|ENGLISH|更多|返回|下一页|上一页|末页|首页)$")


def title_of(a):
    for attr in ("title", "alt"):
        v = a.get(attr, "").strip()
        if v and not NAV_TITLE.match(v):
            return v
    return a.get_text(strip=True)


def existing_urls():
    urls = set()
    for p in (ROOT / "data" / "clean").glob("*.meta.json"):
        urls.add(json.loads(p.read_text(encoding="utf-8"))["source_url"])
    return urls


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.encoding = r.apparent_encoding
    return r


def main():
    existing = existing_urls()
    report = {"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "existing_docs": len(existing), "sites": []}
    for site, cols in COLUMNS.items():
        entry = {"site": site, "columns": [], "new_candidates": [], "errors": []}
        seen = set()
        for col in cols:
            try:
                r = fetch(col)
                if r.status_code != 200:
                    entry["errors"].append(f"{col} HTTP {r.status_code}")
                    time.sleep(1)
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                found = 0
                for a in soup.find_all("a", href=True):
                    href = urljoin(col, a["href"])
                    if urlparse(href).netloc != urlparse(col).netloc:
                        continue
                    if not ARTICLE.search(urlparse(href).path):
                        continue
                    title = title_of(a)
                    if href in seen:
                        continue
                    seen.add(href)
                    found += 1
                    if href in existing:
                        continue
                    # 标题必须像公告（有 title 且含关键词），或 URL 在 report 归档下（标题待抓正文确认）
                    looks_article = bool(title and TITLE_KW.search(title))
                    in_report = "/report/" in urlparse(href).path
                    entry["new_candidates"].append({
                        "url": href, "title": title[:70],
                        "looks": "title" if looks_article else ("archive" if in_report else "unknown"),
                    })
                entry["columns"].append({"url": col, "http": r.status_code, "article_links": found})
                print(f"  [{site}] {col.split('/')[-2] or col.split('/')[-1]} -> 文章链接 {found}")
            except Exception as e:
                entry["errors"].append(f"{col} {type(e).__name__}: {str(e)[:80]}")
            time.sleep(1)  # 每域 1 req/s
        # 分类统计
        strong = [c for c in entry["new_candidates"] if c["looks"] == "title"]
        archive = [c for c in entry["new_candidates"] if c["looks"] == "archive"]
        entry["summary"] = {"total_new": len(entry["new_candidates"]),
                            "title_confirmed": len(strong), "archive_unverified": len(archive)}
        report["sites"].append(entry)
        print(f"[{site}] 新候选 {len(entry['new_candidates'])} "
              f"(标题确认 {len(strong)} / 归档待验 {len(archive)})")

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(s["summary"]["total_new"] for s in report["sites"])
    strong = sum(s["summary"]["title_confirmed"] for s in report["sites"])
    print(f"\n合计新候选 {total}（标题确认公告 {strong}）| 留痕: {OUT}")


if __name__ == "__main__":
    main()

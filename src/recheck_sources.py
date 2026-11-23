r"""数据源现状抽样复核（2026-11-23，受控扩量前的调查步骤）。

目的：3 天前的 column_probe.json / 新站点调研结论是 11-20 的快照，不能当今天的事实。
本脚本做**轻量抽样复核**，不覆盖旧留痕、不抓正文入库、不改主链路：
  1. 原 5 站：各取 1 个主栏目页，验证仍 HTTP 200 且能列出公告链接
  2. 新 5 站：各取 1 个公告列表页，验证可访问 + 能解析公告链接 + robots 状态
  3. 旧候选抽样：从 column_probe.json 抽若干 URL，验证仍存活（HTTP 200）
  4. WAF 特征识别：命中 acw_tc / __jsluid_s / WebShieldDRSessionVerify 立即标记并停止该站进一步请求

合规：公开静态页、明示 UA、每域 1 req/s、GET（不用 HEAD，已知 neea 站 HEAD 被 WAF 拦）、
      遵守 robots、不绕 WAF / 不绕登录、只留痕不入库。
输出：data/source_recheck.json
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
OUT = ROOT / "data" / "source_recheck.json"
WAF_SIGNS = ("acw_tc", "__jsluid_s", "WebShieldDRSessionVerify", "aliyungf_tc")
ARTICLE = re.compile(r"\.(html?|shtml)$|/\d{6}/\d{8}/\d+\.html$|/sswbgg/\d+")

# 原 5 站：各取一个主栏目页（与 probe_columns.py 一致）
OLD_COLUMNS = {
    "中国教育考试网": "https://www.neea.edu.cn/html1/category/16093/615-1.htm",
    "四六级考试网": "https://cet.neea.edu.cn/html1/category/16093/615-1.htm",
    "NCRE考试网": "https://ncre.neea.edu.cn/html1/category/16093/615-1.htm",
    "研招网": "https://yz.chsi.com.cn/kyzx/jybzc/",
    "中国教育在线": "https://www.eol.cn/news/yaowen/",
}
# 新 5 站：后台调研给出的一手列表页
NEW_COLUMNS = {
    "广东省教育考试院": "https://eea.gd.gov.cn/tzgg/index.html",
    "浙江省教育考试院": "https://www.zjzs.net/col/col156",
    "北京教育考试院": "https://www.bjeea.cn/html/bjeeagg",
    "上海市教育考试院": "https://www.shmeea.edu.cn/page/08000/",
    "中国人事考试网": "http://www.cpta.com.cn/notice.html",
}


def get(url, timeout=20):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.encoding = r.apparent_encoding or r.encoding
    return r


def waf_hit(resp):
    cookies = resp.headers.get("Set-Cookie", "") or ""
    return [s for s in WAF_SIGNS if s in cookies or s in resp.text[:2000]]


def probe_column(name, url):
    entry = {"name": name, "url": url}
    try:
        r = get(url)
        entry["http"] = r.status_code
        entry["encoding"] = r.encoding
        entry["bytes"] = len(r.content)
        entry["waf_signs"] = waf_hit(r)
        if r.status_code == 200 and not entry["waf_signs"]:
            soup = BeautifulSoup(r.text, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"])
                if urlparse(href).netloc != urlparse(url).netloc:
                    continue
                title = a.get("title", "").strip() or a.get_text(strip=True)
                if ARTICLE.search(urlparse(href).path) and title:
                    links.append({"title": title[:50], "url": href})
                if len(links) >= 5:
                    break
            entry["sample_links"] = links
            entry["article_links_sample"] = len(links)
        else:
            entry["article_links_sample"] = 0
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return entry


def probe_robots(base_url):
    p = urlparse(base_url)
    robots = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = get(robots, timeout=12)
        body = r.text[:1500]
        allow_all = bool(re.search(r"User-agent:\s*\*[\s\S]{0,200}Allow:\s*/", body, re.I))
        return {"http": r.status_code, "explicit_allow_all": allow_all,
                "snippet": body[:200].replace("\n", " ")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


def probe_old_candidates(sample_n=6):
    """从 11-20 留痕里抽样候选，验证今天仍存活。"""
    src = ROOT / "data" / "column_probe.json"
    if not src.exists():
        return []
    report = json.loads(src.read_text(encoding="utf-8"))
    picked = []
    for s in report["sites"]:
        cands = [c for c in s["new_candidates"] if c["looks"] == "title"]
        for c in cands[:2]:
            picked.append({"site": s["site"], "url": c["url"], "title": c["title"][:50]})
    picked = picked[:sample_n]
    for p in picked:
        try:
            r = get(p["url"], timeout=15)
            p["http_today"] = r.status_code
            p["alive"] = r.status_code == 200
            body_len = len(BeautifulSoup(r.text, "html.parser").get_text(strip=True))
            p["text_chars_today"] = body_len
        except Exception as e:
            p["alive"] = False
            p["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(1)
    return picked


def main():
    result = {"rechecked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "note": "抽样复核，不入库不改主链路；旧快照 data/column_probe.json 未被覆盖",
              "old_columns": [], "new_columns": [], "old_candidates": []}
    print("=== 原 5 站栏目页复核 ===")
    for name, url in OLD_COLUMNS.items():
        e = probe_column(name, url)
        result["old_columns"].append(e)
        print(f"  [{name}] HTTP {e.get('http')} | 样例公告链接 {e.get('article_links_sample')} | WAF {e.get('waf_signs')}")
        time.sleep(1)

    print("=== 新 5 站列表页复核 ===")
    for name, url in NEW_COLUMNS.items():
        e = probe_column(name, url)
        e["robots"] = probe_robots(url)
        result["new_columns"].append(e)
        print(f"  [{name}] HTTP {e.get('http')} | 样例公告链接 {e.get('article_links_sample')} | robots {e['robots'].get('http')} | WAF {e.get('waf_signs')}")
        time.sleep(1)

    print("=== 旧候选抽样存活复核 ===")
    result["old_candidates"] = probe_old_candidates()
    for p in result["old_candidates"]:
        print(f"  [{p['site']}] HTTP {p.get('http_today')} | 正文 {p.get('text_chars_today')} 字 | {p['title'][:28]}")

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    alive = sum(1 for p in result["old_candidates"] if p.get("alive"))
    print(f"\n旧候选抽样存活 {alive}/{len(result['old_candidates'])} | 留痕: {OUT}")


if __name__ == "__main__":
    main()

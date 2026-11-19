r"""候选考试站点合规探测：先查 robots.txt，允许才抓，限速+明示 UA+全程留痕。
运行：.venv\Scripts\python.exe src\probe_sites.py
输出：data\probe_report.json + 控制台摘要
合规：不碰登录态/题库/真题；robots 4xx 放行、5xx 全禁（RFC9309）；每域 1 请求+1s 延迟。
"""
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura

TZ = timezone(timedelta(hours=8))
UA = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research)"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

CANDIDATES = [
    {"name": "中国教育考试网", "url": "https://www.neea.edu.cn/", "region": "境内",
     "note": "教育部教育考试院官方，四六级/考研等考试公告"},
    {"name": "全国大学英语四六级考试网", "url": "https://cet.neea.edu.cn/", "region": "境内",
     "note": "CET 官方站，最贴合 StudyDesk 的六级场景"},
    {"name": "中国研究生招生信息网", "url": "https://yz.chsi.com.cn/", "region": "境内",
     "note": "考研官方，研招信息"},
    {"name": "中国人事考试网", "url": "http://www.cpta.com.cn/", "region": "境内",
     "note": "职业资格考试官方"},
    {"name": "全国计算机等级考试网", "url": "https://ncre.neea.edu.cn/", "region": "境内",
     "note": "NCRE 官方，证书考试场景"},
    {"name": "中国教育在线", "url": "https://www.eol.cn/", "region": "境内",
     "note": "教育资讯门户，备选"},
]


def fetch(url, timeout=20):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def judge_robots(base, target):
    """返回 (robots_status, allowed, reason) —— RFC9309: 4xx 放行, 5xx 全禁。"""
    robots_url = urljoin(base, "/robots.txt")
    r, err = fetch(robots_url, timeout=15)
    if r is None:
        return None, False, f"robots 取不到({err})，保守跳过"
    status = r.status_code
    if status >= 500:
        return status, False, "robots 5xx → 视为全禁(RFC9309)"
    if 400 <= status < 500:
        return status, True, "robots 4xx → 视为无规则可爬(RFC9309)"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    rp.parse(r.text.splitlines())
    allowed = rp.can_fetch(UA, target)
    return status, allowed, "robots 200 → " + ("允许" if allowed else "禁止该路径")


def probe(c):
    base = f"{urlparse(c['url']).scheme}://{urlparse(c['url']).netloc}/"
    result = {**c, "fetched_at": datetime.now(TZ).isoformat(timespec="seconds")}
    rs, allowed, reason = judge_robots(base, c["url"])
    result["robots_status"] = rs
    result["robots_reason"] = reason
    result["allowed"] = allowed
    if not allowed:
        result["verdict"] = "跳过（robots 不允许）"
        result["http_status"] = None
        result["extract_chars"] = 0
        print(f"  robots: {reason} → 跳过")
        return result
    time.sleep(1 + random.random() * 0.5)
    r, err = fetch(c["url"])
    if r is None:
        result["verdict"] = f"不可达：{err}"
        result["http_status"] = None
        result["extract_chars"] = 0
        print(f"  抓取失败: {err}")
        return result
    result["http_status"] = r.status_code
    result["final_url"] = r.url
    result["html_bytes"] = len(r.content)
    if r.status_code != 200:
        result["verdict"] = f"HTTP {r.status_code}"
        result["extract_chars"] = 0
        print(f"  HTTP {r.status_code}")
        return result
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False, favor_recall=True)
    result["extract_chars"] = len(text) if text else 0
    result["title"] = (text.splitlines()[0][:60] if text else "")
    if not text or len(text) < 100:
        result["verdict"] = "抓到 HTML 但抽取正文过少（疑似 JS 壳/首页导航页）"
    else:
        result["verdict"] = "适合：静态正文可抽取"
    print(f"  HTTP {r.status_code} | HTML {len(r.content)//1024}KB | 正文 {result['extract_chars']} 字 | {result['verdict']}")
    return result


def main():
    print("=== 候选考试站点合规探测（每域仅 1 页 + robots）===")
    print("UA:", UA)
    results = [probe(c) for c in CANDIDATES]
    out = Path(__file__).resolve().parent.parent / "data" / "probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===")
    for r in results:
        print(f"{r['name']:<16} {r['region']:<4} robots={str(r['robots_status']):<5} "
              f"HTTP={str(r['http_status']):<5} 正文={r['extract_chars']:<5} {r['verdict']}")
    print(f"\n留痕: {out}")


if __name__ == "__main__":
    main()

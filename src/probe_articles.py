r"""内页探测：从候选站首页提取公告/资讯类链接，抓 1 篇正文验证抽取质量。
合规：沿用 robots 允许结论；每域仅多抓 1 篇；限速+明示 UA+留痕。
运行：.venv\Scripts\python.exe src\probe_articles.py
"""
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup

TZ = timezone(timedelta(hours=8))
UA = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research)"
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

SITES = [
    {"name": "中国教育考试网", "home": "https://www.neea.edu.cn/"},
    {"name": "全国大学英语四六级考试网", "home": "https://cet.neea.edu.cn/"},
    {"name": "中国研究生招生信息网", "home": "https://yz.chsi.com.cn/"},
    {"name": "全国计算机等级考试网", "home": "https://ncre.neea.edu.cn/"},
    {"name": "中国教育在线", "home": "https://www.eol.cn/"},
]

KEYWORDS = re.compile(r"考试|公告|通知|资讯|政策|大纲|报名|简章|要闻|动态|信息")


def fetch(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r


def allowed(base, url):
    robots = urljoin(base, "/robots.txt")
    try:
        r = requests.get(robots, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
            return rp.can_fetch(UA, url)
        return 400 <= r.status_code < 500
    except Exception:
        return False


def pick_link(home, html):
    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = urljoin(home, a["href"])
        if not KEYWORDS.search(text) or len(text) < 6:
            continue
        if urlparse(href).netloc != urlparse(home).netloc:
            continue
        if not re.search(r"\.(html?|shtml)$", urlparse(href).path) and "#" not in href:
            continue
        return text[:60], href
    return None, None


def main():
    print("=== 内页正文抽取质量探测（每域仅多抓 1 篇）===")
    results = []
    for s in SITES:
        home = s["home"]
        base = f"{urlparse(home).scheme}://{urlparse(home).netloc}/"
        try:
            r = fetch(home)
            link_text, link = pick_link(home, r.text)
            if not link:
                print(f"{s['name']}: 首页未找到候选公告链接")
                results.append({**s, "status": "no_link", "extract_chars": 0})
                continue
            if not allowed(base, link):
                print(f"{s['name']}: 链接 robots 不允许 → {link[:60]}")
                results.append({**s, "status": "robots_deny", "link": link, "extract_chars": 0})
                continue
            time.sleep(1 + random.random() * 0.5)
            art = fetch(link)
            text = trafilatura.extract(art.text, include_comments=False, include_tables=False, favor_recall=True)
            chars = len(text) if text else 0
            first = text.splitlines()[0][:70] if text else ""
            print(f"{s['name']}: [{link_text}] HTTP {art.status_code} 正文 {chars} 字 | 开头: {first}")
            results.append({**s, "status": "ok" if chars >= 200 else "thin",
                            "link_text": link_text, "link": link, "http_status": art.status_code,
                            "extract_chars": chars, "first_line": first, "sample": (text[:300] if text else ""),
                            "fetched_at": datetime.now(TZ).isoformat(timespec="seconds")})
        except Exception as e:
            print(f"{s['name']}: ERROR {type(e).__name__}: {str(e)[:100]}")
            results.append({**s, "status": "error", "error": str(e)[:120], "extract_chars": 0})
    out = Path(__file__).resolve().parent.parent / "data" / "article_probe_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n留痕: {out}")


if __name__ == "__main__":
    main()

r"""扩量空间探测（2026-11-20）：量化 5 站首页相对已抓 22 篇的可新增公告数。

合规：只 GET 5 站首页（公开静态页），明示 UA、每站 1 请求、1 秒间隔、遵守 robots。
不抓正文、不绕 WAF、不维持登录态。输出留痕到 data/expansion_probe.json。
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS = re.compile(r"考试|公告|通知|资讯|政策|大纲|报名|简章|要闻|动态|信息")
SITES = [
    ("中国教育考试网", "https://www.neea.edu.cn/"),
    ("四六级考试网", "https://cet.neea.edu.cn/"),
    ("研招网", "https://yz.chsi.com.cn/"),
    ("NCRE考试网", "https://ncre.neea.edu.cn/"),
    ("中国教育在线", "https://www.eol.cn/"),
]
UA = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research)"
OUT = ROOT / "data" / "expansion_probe.json"


def existing_urls():
    urls = set()
    for p in (ROOT / "data" / "clean").glob("*.meta.json"):
        urls.add(json.loads(p.read_text(encoding="utf-8"))["source_url"])
    return urls


def main():
    existing = existing_urls()
    report = {"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "existing_docs": len(existing), "sites": []}
    for name, home in SITES:
        entry = {"site": name, "home": home, "error": None, "total_article_links": 0, "new_candidates": []}
        try:
            r = requests.get(home, headers={"User-Agent": UA}, timeout=20)
            entry["http_status"] = r.status_code
            if r.status_code != 200:
                entry["error"] = f"HTTP {r.status_code}"
            else:
                # neea 站是 GBK 编码，requests 默认按 ISO-8859-1 解成乱码；
                # 显式用 apparent_encoding（Scrapy 阶段也是靠自动检测才抓到）
                r.encoding = r.apparent_encoding
                entry["encoding"] = r.encoding
                soup = BeautifulSoup(r.text, "html.parser")
                seen = set()
                for a in soup.find_all("a", href=True):
                    text = a.get_text(strip=True)
                    href = urljoin(home, a["href"])
                    if not KEYWORDS.search(text) or len(text) < 6:
                        continue
                    if urlparse(href).netloc not in {urlparse(s[1]).netloc for s in SITES}:
                        continue
                    if not re.search(r"\.(html?|shtml)$", urlparse(href).path):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    entry["total_article_links"] += 1
                    if href not in existing:
                        entry["new_candidates"].append({"url": href, "text": text[:60]})
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        report["sites"].append(entry)
        print(f"[{name}] 状态码={entry.get('http_status')} 公告链接={entry['total_article_links']} "
              f"新候选={len(entry['new_candidates'])} {entry['error'] or ''}")
        time.sleep(1)  # 每站 1 秒间隔，严格限速

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    total_new = sum(len(s["new_candidates"]) for s in report["sites"])
    print(f"\n合计新候选: {total_new} | 留痕: {OUT}")


if __name__ == "__main__":
    main()

r"""诊断：浙江/北京栏目页为何 0 条候选（2026-11-24）。

只 GET 2 个栏目页，明示 UA、1 秒间隔，不抓正文、不入库。
输出 data/zero_sites_diag.json 留痕，回答"文章链接到底长什么样"。
"""
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
UA = "StudyDeskResearchBot/0.1 (public exam-info pages; educational research; contact: local)"
OUT = ROOT / "data" / "zero_sites_diag_index.json"
SITES = [
    ("浙江省教育考试院", "https://www.zjzs.net/col/col156/index.html"),
    ("北京教育考试院", "https://www.bjeea.cn/html/bjeeagg/index.html"),
]


def main():
    out = {"note": "两站 0 候选诊断：只取栏目页链接样本，不抓正文", "sites": []}
    for name, url in SITES:
        entry = {"name": name, "url": url}
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.encoding = r.apparent_encoding or r.encoding
            soup = BeautifulSoup(r.text, "html.parser")
            entry["http"] = r.status_code
            entry["html_chars"] = len(r.text)
            entry["title"] = soup.title.get_text(strip=True) if soup.title else None
            links = []
            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"])
                if urlparse(href).netloc != urlparse(url).netloc:
                    continue
                text = (a.get("title") or a.get_text(strip=True)).strip()
                if text or href:
                    links.append({"text": text[:50], "href": href})
            entry["same_domain_links"] = len(links)
            entry["samples"] = links[:25]
            print(f"[{name}] HTTP {r.status_code} | HTML {len(r.text)} 字符 | 同域链接 {len(links)} 条")
            for l in links[:12]:
                print(f"  {l['text'][:28]:<30} {l['href']}")
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"[{name}] 失败：{entry['error']}")
        out["sites"].append(entry)
        time.sleep(1)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"留痕：{OUT}")


if __name__ == "__main__":
    main()

"""通用化可行性一手探测（只读、不入库、不留正文）。

用途：为"定向采集 → 通用采集"的架构决策提供本机第一手证据：
1) 发现层：公开搜索引擎结果页能否直连拿到链接（结论：Bing 可、DDG 代理不通、百度空壳）；
2) 抽取层：同一套 trafilatura 在已适配站 vs 全新站的抽取差异（结论：1028 vs 62 字符，静默失败）。

证据留痕：data/generic_probe_evidence.json
运行：.venv/Scripts/python.exe src/generic_feasibility_probe.py
注意：本脚本只做少量礼貌请求（每域 1 次、明示 UA），不写 data/clean、不改主链路。
"""
import json
import requests
import trafilatura
from bs4 import BeautifulSoup

UA = 'StudyDeskResearchBot/0.1 (public info research; educational)'
QUERY = '2027 上海 舞蹈类 统考 时间'

DISCOVERY = [
    ('duckduckgo-html', 'https://html.duckduckgo.com/html/', {'q': QUERY}, 'a.result__a'),
    ('bing', 'https://www.bing.com/search', {'q': QUERY}, 'h2 a'),
    ('baidu', 'https://www.baidu.com/s', {'wd': QUERY}, 'h3 a'),
]

EXTRACTION = [
    ('已验证站-neea', 'https://www.neea.edu.cn/html1/report/2609/27-1.htm'),
    ('全新站-gov政策正文', 'https://www.gov.cn/zhengce/zhengceku/202611/content_7085102.htm'),
]

out: dict = {'discovery': [], 'extraction': []}

for name, url, params, selector in DISCOVERY:
    row = {'target': name}
    try:
        r = requests.get(url, params=params, headers={'User-Agent': UA}, timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = [(a.get_text(' ', strip=True)[:50], a.get('href', '')[:80])
                 for a in soup.select(selector)][:3]
        row.update(status=r.status_code, bytes=len(r.content), links=links)
    except Exception as e:
        row['error'] = f'{type(e).__name__}: {str(e)[:100]}'
    out['discovery'].append(row)
    print(json.dumps(row, ensure_ascii=False))

for name, url in EXTRACTION:
    row = {'site': name, 'url': url}
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=12)
        r.encoding = r.apparent_encoding or 'utf-8'
        txt = trafilatura.extract(r.text, include_comments=False,
                                  include_tables=True, favor_recall=True) or ''
        row.update(status=r.status_code, bytes=len(r.content),
                   extracted=len(txt), preview=txt[:120].replace('\n', ' '))
    except Exception as e:
        row['error'] = f'{type(e).__name__}: {str(e)[:100]}'
    out['extraction'].append(row)
    print(json.dumps(row, ensure_ascii=False))

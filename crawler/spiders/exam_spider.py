"""5 站配置驱动 spider：列表页提公告链接 → 详情页交 pipeline 抽取。
配置来自实测《考试站点实测选型结论.md》，每站限额防止过量抓取。
"""
import re
from urllib.parse import urljoin, urlparse

import scrapy
from bs4 import BeautifulSoup

from crawler.items import RawPageItem

KEYWORDS = re.compile(r"考试|公告|通知|资讯|政策|大纲|报名|简章|要闻|动态|信息")

# 实测确定的 5 站（全部境内、robots 允许、公告正文抽取质量过关）
SITES = [
    {"name": "中国教育考试网", "home": "https://www.neea.edu.cn/"},
    {"name": "四六级考试网", "home": "https://cet.neea.edu.cn/"},
    {"name": "研招网", "home": "https://yz.chsi.com.cn/"},
    {"name": "NCRE考试网", "home": "https://ncre.neea.edu.cn/"},
    {"name": "中国教育在线", "home": "https://www.eol.cn/"},
]
MAX_ARTICLES_PER_SITE = 6  # MVP 规模：5 站 × 6 = 30 篇上限


class ExamSpider(scrapy.Spider):
    name = "exam"
    allowed_domains = [urlparse(s["home"]).netloc for s in SITES]
    start_urls = [s["home"] for s in SITES]
    custom_settings = {"DEPTH_LIMIT": 2}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counts = {s["home"]: 0 for s in SITES}
        self.site_of = {s["home"]: s["name"] for s in SITES}
        self.home_of_netloc = {urlparse(s["home"]).netloc: s["home"] for s in SITES}

    def parse(self, response):
        """首页/列表页：提取公告类链接，限量跟进。"""
        home = self.home_of_netloc.get(urlparse(response.url).netloc, response.url)
        soup = BeautifulSoup(response.text, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=True):
            if self.counts.get(home, 0) >= MAX_ARTICLES_PER_SITE:
                break
            text = a.get_text(strip=True)
            href = urljoin(response.url, a["href"])
            if not KEYWORDS.search(text) or len(text) < 6:
                continue
            if urlparse(href).netloc not in self.allowed_domains:
                continue
            if not re.search(r"\.(html?|shtml)$", urlparse(href).path):
                continue
            if href in seen:
                continue
            seen.add(href)
            self.counts[home] += 1
            self.logger.info(f"[{self.site_of.get(home)}] 跟进公告 {self.counts[home]}/{MAX_ARTICLES_PER_SITE}: {text[:40]}")
            yield scrapy.Request(href, callback=self.parse_article, cb_kwargs={"home": home, "link_text": text[:80]})

    def parse_article(self, response, home, link_text):
        """详情页：只产出原始 HTML + 上下文，正文抽取交给 pipeline。"""
        yield RawPageItem(
            source_url=response.url,
            html=response.text,
            http_status=response.status,
            site_name=self.site_of.get(home, ""),
            title_hint=link_text,
            meta={"referer_home": home},
        )

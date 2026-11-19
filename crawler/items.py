"""数据项定义：原始 HTML 与抽取后的结构化内容分离，元数据 sidecar 独立。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

TZ = timezone(timedelta(hours=8))


@dataclass
class RawPageItem:
    """Scrapy spider 产出的原始页：只保留 HTML 与抓取上下文，不做正文抽取。"""
    source_url: str
    html: str
    http_status: int
    fetched_at: str = field(default_factory=lambda: datetime.now(TZ).isoformat(timespec="seconds"))
    site_name: str = ""
    title_hint: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class CleanDoc:
    """清洗后文档：正文 Markdown + 元数据（落盘为 .md + .meta.json sidecar）。"""
    doc_id: str
    source_url: str
    title: str
    markdown: str
    site_name: str = ""
    fetched_at: str = ""
    http_status: int = 200
    parser_version: str = "trafilatura-2.0.0"
    index_version: str = "v1"
    lang: str = "zh"
    content_sha256: str = ""
    pii_redactions: int = 0
    text_chars: int = 0
    chunk_ids: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

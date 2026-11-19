"""Pipeline 链：抽取 -> 清洗脱敏 -> 去重 -> 落盘。
中间契约：RawPageItem（原始HTML）→ 抽取 → CleanDoc（Markdown + sidecar）→ 落盘。
抓取器与抽取层解耦，将来换抓取方式本链路不动。
"""
import json
import re
from pathlib import Path

import trafilatura

from crawler.cleaning import content_fingerprint, mask_pii, normalize_url
from crawler.items import CleanDoc, RawPageItem

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CLEAN_DIR = DATA_DIR / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

PARSER_VERSION = "trafilatura-2.0.0"
INDEX_VERSION = "v1"


class ExtractPipeline:
    """trafilatura 正文抽取（FineWeb 同款）。失败时回退 readabilipy 思路的宽松模式。"""
    def process_item(self, item, spider):
        if not isinstance(item, RawPageItem):
            return item
        text = trafilatura.extract(
            item.html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            url=item.source_url,
        )
        if not text or len(text) < 100:
            spider.logger.warning(f"抽取过少，丢弃: {item.source_url}")
            raise DropItem(f"extract too thin: {item.source_url}")
        title = text.splitlines()[0].strip() if text.splitlines() else item.title_hint
        item.meta["markdown"] = text
        item.meta["title"] = title[:120]
        return item


class CleanPipeline:
    """URL 规范化 + 中文 PII 脱敏（B1）+ 内容指纹（B2）。"""
    def process_item(self, item, spider):
        if "markdown" not in item.meta:
            return item
        normalized_url = normalize_url(item.source_url)
        clean_text, hits = mask_pii(item.meta["markdown"])
        item.meta["normalized_url"] = normalized_url
        item.meta["markdown"] = clean_text
        item.meta["pii_hits"] = hits
        item.meta["fingerprint"] = content_fingerprint(clean_text)
        if hits:
            spider.logger.info(f"PII 脱敏 {len(hits)} 处: {item.source_url}")
        return item


class DedupPipeline:
    """URL + 内容指纹双重去重（B2）。"""
    def __init__(self):
        self.seen_urls = set()
        self.seen_hashes = set()

    def process_item(self, item, spider):
        if "markdown" not in item.meta:
            return item
        url = item.meta["normalized_url"]
        fp = item.meta["fingerprint"]
        if url in self.seen_urls or fp in self.seen_hashes:
            raise DropItem(f"duplicate: {url}")
        self.seen_urls.add(url)
        self.seen_hashes.add(fp)
        return item


class StorePipeline:
    """落盘 Markdown + sidecar JSON（B3 元数据边界）。"""
    def process_item(self, item, spider):
        if "markdown" not in item.meta:
            return item
        from hashlib import sha256
        doc_id = sha256(item.meta["normalized_url"].encode("utf-8")).hexdigest()[:16]
        hits = item.meta.get("pii_hits", [])
        doc = CleanDoc(
            doc_id=doc_id,
            source_url=item.meta["normalized_url"],
            title=item.meta["title"],
            markdown=item.meta["markdown"],
            site_name=item.site_name,
            fetched_at=item.fetched_at,
            http_status=item.http_status,
            parser_version=PARSER_VERSION,
            index_version=INDEX_VERSION,
            lang="zh",
            content_sha256=item.meta["fingerprint"],
            pii_redactions=len(hits),
            text_chars=len(item.meta["markdown"]),
        )
        md_path = CLEAN_DIR / f"{doc_id}.md"
        meta_path = CLEAN_DIR / f"{doc_id}.meta.json"
        md_path.write_text(doc.markdown, encoding="utf-8")
        meta_path.write_text(json.dumps(doc.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        spider.logger.info(f"落盘 {doc_id} | {doc.title[:30]} | {doc.text_chars} 字")
        return doc


from scrapy.exceptions import DropItem  # noqa: E402

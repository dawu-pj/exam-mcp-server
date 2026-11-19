"""BM25 检索层：jieba 中文分词 + 元数据过滤（B3）+ 带出处返回。
先验 recall@k，不接 embedding（已定 P0-5）。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "clean"
STOPWORDS = set("的 了 和 是 在 有 及 与 为 对 中 等 年 月 日 将 已 于 这 该 其 更 后 前 新".split())


@dataclass
class Hit:
    doc_id: str
    title: str
    source_url: str
    site_name: str
    score: float
    markdown: str

    def cite(self) -> str:
        return f"[{self.title}]({self.source_url})"


def tokenize(text: str):
    return [w for w in jieba.lcut(text) if w.strip() and w not in STOPWORDS and not re.match(r"^\W+$", w)]


class BM25Retriever:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.docs = []
        for meta_path in sorted(data_dir.glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            md_path = meta_path.with_suffix("").with_suffix(".md")
            if not md_path.exists():
                continue
            self.docs.append({**meta, "markdown": md_path.read_text(encoding="utf-8")})
        if not self.docs:
            raise RuntimeError(f"没有清洗后的文档：{data_dir}（先跑 scrapy crawl exam）")
        self.corpus = [tokenize(d["title"] + " " + d["markdown"]) for d in self.docs]
        self.bm25 = BM25Okapi(self.corpus)

    def search(self, query: str, top_k: int = 5, site: str | None = None) -> list[Hit]:
        scores = self.bm25.get_scores(tokenize(query))
        idx_scores = list(enumerate(scores))
        if site:
            idx_scores = [(i, s) for i, s in idx_scores if self.docs[i].get("site_name") == site]
        idx_scores.sort(key=lambda x: x[1], reverse=True)
        hits = []
        for i, score in idx_scores[:top_k]:
            if score <= 0:
                continue
            d = self.docs[i]
            hits.append(Hit(
                doc_id=d["doc_id"], title=d["title"], source_url=d["source_url"],
                site_name=d.get("site_name", ""), score=round(float(score), 3), markdown=d["markdown"],
            ))
        return hits


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "四六级考试报名"
    r = BM25Retriever()
    print(f"索引文档数: {len(r.docs)}")
    print(f"查询: {q}\n")
    for i, h in enumerate(r.search(q, top_k=5), 1):
        print(f"{i}. [{h.score}] {h.title}")
        print(f"   出处: {h.source_url}")
        print(f"   摘要: {h.markdown[:120].replace(chr(10), ' ')}...\n")

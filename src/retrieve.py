"""BM25 检索层：jieba 中文分词 + 元数据过滤（B3）+ 带出处返回。
先验 recall@k，不接 embedding（已定 P0-5）。

2026-11-20 字段加权接入：标题/正文分字段 BM25，score = alpha*title + (1-alpha)*body。
依据：阶段2实验 `tests/evaluate_field_weight.py` —— 预设 alpha=0.3 不显著，
但敏感性扫描发现 alpha=0.5，经 LOOCV 无偏复核（15/15 次选中、doc_hit@1
53%→87%、配对 95%CI [+0.067,+0.600] 下界>0）确认有效；负例拒答保持 100%。
不改接口：search(query, top_k, site) 与 Hit 结构不变，MCP 工具无需改动。
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
    def __init__(self, data_dir: Path = DATA_DIR, alpha: float = 0.5):
        """alpha: 标题字段权重（0=纯正文；0.5=LOOCV 验证过的最优值）。

        注意 sidecar 的 title 就是 markdown 首行（22/22 篇实测），旧实现
        `title + markdown` 让标题隐式算 2 遍且被长正文归一化稀释；分字段后
        标题独立打分，短文档的标题关键词不再淹没在 2 万字正文里。
        """
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha 必须在 [0,1]，收到 {alpha}")
        self.alpha = alpha
        self.docs = []
        for meta_path in sorted(data_dir.glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            md_path = meta_path.with_suffix("").with_suffix(".md")
            if not md_path.exists():
                continue
            self.docs.append({**meta, "markdown": md_path.read_text(encoding="utf-8")})
        if not self.docs:
            raise RuntimeError(f"没有清洗后的文档：{data_dir}（先跑 scrapy crawl exam）")
        # 两字段各自建索引；body 保留完整 markdown（含首行标题），与基线文本一致，
        # A/B 唯一差异是「标题是否独立成字段」。
        self.title_bm25 = BM25Okapi([tokenize(d["title"]) for d in self.docs])
        self.body_bm25 = BM25Okapi([tokenize(d["markdown"]) for d in self.docs])

    def search(self, query: str, top_k: int = 5, site: str | None = None) -> list[Hit]:
        q = tokenize(query)
        scores = self.alpha * self.title_bm25.get_scores(q) + (1 - self.alpha) * self.body_bm25.get_scores(q)
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

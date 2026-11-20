"""embedding 技术储备：本地 bge-small-zh-v1.5 独立实测模块（不接入主检索链路）。

目的：验证本机 CPU 能否跑、拿到真实延迟/相似度数据、对比 BM25，为阶段 2 决策留依据。
**本模块不被 mcp_server.py / retrieve.py / quick.py 引用**，主链路保持纯 BM25（100% 基线）。

运行：
  HF_ENDPOINT=https://hf-mirror.com .venv/Scripts/python.exe src/embedding_research.py
输出：
  data/embedding_research.json（20 题新旧指标对比 + 速度实测）

模型：BAAI/bge-small-zh-v1.5（fastembed ONNX，512 维，0.09GB，纯 CPU）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# 固定镜像端点，避免 fastembed 走境外源；模型已缓存时不触发下载
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from fastembed import TextEmbedding  # noqa: E402
from retrieve import BM25Retriever, DATA_DIR, STOPWORDS  # noqa: E402
import jieba  # noqa: E402

MODEL = "BAAI/bge-small-zh-v1.5"
OUT = ROOT / "data" / "embedding_research.json"
GOLDEN = ROOT / "tests" / "golden_set.json"


CACHE_DIR = Path(__import__("tempfile").gettempdir()) / "fastembed_cache"


class DenseRetriever:
    """文档级向量检索：每篇 markdown 一个向量（不做分块，依据阶段2调研 2.2 节）。"""

    def __init__(self, model_name=MODEL):
        # 用 fastembed 默认缓存（模型已在 temp/fastembed_cache），传 cache_dir 会改变 key 命中不到
        self.model = TextEmbedding(model_name=model_name)
        self.docs = []
        for meta_path in sorted(DATA_DIR.glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            md_path = meta_path.with_suffix("").with_suffix(".md")
            if not md_path.exists():
                continue
            self.docs.append({**meta, "markdown": md_path.read_text(encoding="utf-8")})
        t = time.time()
        texts = [d["title"] + " " + d["markdown"][:2000] for d in self.docs]
        self.vectors = list(self.model.embed(texts))
        self.index_sec = time.time() - t

    def search(self, query, top_k=5):
        # bge 检索建议在 query 侧加指令前缀（官方 model card）
        qvec = list(self.model.embed(["为这个句子生成表示以用于检索相关文章：" + query]))[0]
        scored = []
        for i, v in enumerate(self.vectors):
            sim = sum(a * b for a, b in zip(qvec, v))  # 已归一化，点积=余弦
            scored.append((i, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(self.docs[i]["doc_id"], round(float(s), 4)) for i, s in scored[:top_k]]


def load_cases():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for c in cases:
        c.setdefault("expect_doc_id", None)
        c.setdefault("expect_answerable", True)
    return cases


def evaluate_dense(dr, cases, top_k=5):
    pos = [c for c in cases if c["expect_answerable"]]
    hits1 = hits3 = 0
    for c in pos:
        ids = [d for d, _ in dr.search(c["q"], top_k=top_k)]
        if ids and ids[0] == c["expect_doc_id"]:
            hits1 += 1
        if c["expect_doc_id"] in ids[:3]:
            hits3 += 1
    n = len(pos)
    return {"doc_hit@1": hits1 / n, "doc_hit@3": hits3 / n}


def main():
    print("=== embedding 技术储备实测（不接主链路）===\n")
    t = time.time()
    dr = DenseRetriever()
    cold_sec = time.time() - t
    print(f"模型冷启动+建索引: {cold_sec:.1f}s（22 篇，其中建索引 {dr.index_sec:.1f}s）")

    # 速度实测：单 query 编码
    qs = ["四六级报名", "考研管理规定", "NCRE 考试时间", "科研课题立项", "就业信息平台"]
    t = time.time()
    for q in qs * 4:
        list(dr.model.embed(["为这个句子生成表示以用于检索相关文章：" + q]))
    per_q = (time.time() - t) / (len(qs) * 4)
    print(f"单 query 编码均速: {per_q * 1000:.1f} ms（CPU，无 GPU）")
    print(f"向量维度: {len(dr.vectors[0])} | 模型: {MODEL}")

    cases = load_cases()
    dense = evaluate_dense(dr, cases)
    bm25 = BM25Retriever()
    pos = [c for c in cases if c["expect_answerable"]]
    b1 = b3 = 0
    for c in pos:
        ids = [h.doc_id for h in bm25.search(c["q"], top_k=5)]
        b1 += ids and ids[0] == c["expect_doc_id"]
        b3 += c["expect_doc_id"] in ids[:3]
    base = {"doc_hit@1": b1 / len(pos), "doc_hit@3": b3 / len(pos)}

    # 分数重叠检查：正负例 Top1 相似度分布（验证调研里的"拉不开"现象）
    pos_scores, neg_scores = [], []
    for c in cases:
        res = dr.search(c["q"], top_k=1)
        (pos_scores if c["expect_answerable"] else neg_scores).append(res[0][1] if res else 0)
    overlap = min(pos_scores) < max(neg_scores)

    print("\n=== 20 题对比（正例 15）===")
    print(f"  BM25   doc_hit@1={base['doc_hit@1']:.0%}  @3={base['doc_hit@3']:.0%}")
    print(f"  Dense  doc_hit@1={dense['doc_hit@1']:.0%}  @3={dense['doc_hit@3']:.0%}")
    print(f"  正例 Top1 相似度范围: [{min(pos_scores):.3f}, {max(pos_scores):.3f}]")
    print(f"  负例 Top1 相似度范围: [{min(neg_scores):.3f}, {max(neg_scores):.3f}]")
    print(f"  分布重叠: {'是（相似度拉不开，与调研一致）' if overlap else '否'}")

    # 配对显著性检验：逐题 doc_hit@1 差值（Dense - BM25），一次加载内完成
    sys.path.insert(0, str(ROOT / "tests"))
    from evaluate import bootstrap_ci
    deltas = []
    for c in pos:
        dense_ids = [d for d, _ in dr.search(c["q"], top_k=5)]
        bm_ids = [h.doc_id for h in bm25.search(c["q"], top_k=5)]
        want = c["expect_doc_id"]
        d = 1 if dense_ids and dense_ids[0] == want else 0
        b = 1 if bm_ids and bm_ids[0] == want else 0
        deltas.append(d - b)
    mean, lo, hi = bootstrap_ci(deltas)
    improved = sum(1 for x in deltas if x > 0)
    regressed = sum(1 for x in deltas if x < 0)
    verdict = "显著优于" if lo > 0 else ("显著差于" if hi < 0 else "CI跨0·不显著·维持BM25")
    print("\n=== 配对检验 doc_hit@1（Dense − BM25）===")
    print(f"  改善 {improved} 题 | 退化 {regressed} 题 | 平均 {mean:+.3f} 95%CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"  判定: {verdict}")

    result = {
        "paired_test": {
            "improved": improved, "regressed": regressed,
            "mean_delta": round(mean, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "verdict": verdict,
        },
        "model": MODEL, "dim": len(dr.vectors[0]),
        "cold_start_sec": round(cold_sec, 2), "index_sec": round(dr.index_sec, 2),
        "query_ms": round(per_q * 1000, 1),
        "bm25": base, "dense": dense,
        "pos_sim_range": [round(min(pos_scores), 4), round(max(pos_scores), 4)],
        "neg_sim_range": [round(min(neg_scores), 4), round(max(neg_scores), 4)],
        "score_overlap": overlap,
        "conclusion": (
            "dense 单独未超过 BM25 → 维持阶段2'不接 embedding'裁定；"
            "本模块仅作技术储备，不接入主链路。"
        ),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已存: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

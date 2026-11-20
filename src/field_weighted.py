r"""字段加权实验模块（阶段2追加实验，2026-11-20）——现为薄封装，复用主链路实现。

实验结果：alpha=0.5 经 LOOCV 无偏复核确认有效，已并入 retrieve.BM25Retriever
（默认 alpha=0.5），故本模块只保留实验入口，逻辑与主链路同源，避免两份代码漂移。

完整实验脚本：tests/evaluate_field_weight.py
实验留痕：data/field_weighted_alpha03.json、阶段2调研与实施方案.md 第七章

依据：BM25F（Robertson et al., CIKM 2004）多字段 BM25 加权融合的经典做法；
SIGIR Forum 回顾文 http://sigir.org/wp-content/uploads/2017/06/p0011.pdf
"""
from pathlib import Path

from retrieve import DATA_DIR, BM25Retriever


class FieldWeightedRetriever(BM25Retriever):
    """实验兼容入口：等价于 BM25Retriever(alpha=alpha)。"""

    def __init__(self, data_dir: Path = DATA_DIR, alpha: float = 0.3):
        super().__init__(data_dir=data_dir, alpha=alpha)


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "2027年全国硕士研究生招生工作管理规定"
    r = FieldWeightedRetriever(alpha=0.5)
    print(f"索引文档数: {len(r.docs)} | alpha={r.alpha} | 查询: {q}\n")
    for i, h in enumerate(r.search(q, top_k=5), 1):
        print(f"{i}. [{h.score}] {h.title[:60]}")
        print(f"   {h.source_url}\n")

r"""检索评估 v2：站点级（诊断）+ 文档级（主指标）+ 负例 + 配对显著性检验。

运行：
  .venv\Scripts\python.exe tests\evaluate.py                    # 单系统评估
  .venv\Scripts\python.exe tests\evaluate.py --compare a.json b.json   # A/B 配对比较

设计依据：阶段2调研 2.1 节。5 题全对的 95%CI 是 [47.8%,100%]，故扩到 20 题。
站点命中率降级为诊断（发现不了"同站不同篇"），文档命中/排序位置为主指标。
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from retrieve import BM25Retriever  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden_set.json"
TOP_K = 5


def load_cases():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # 向后兼容旧 5 题：缺新字段时按"正例 + 非陷阱 + easy"补全
    for c in cases:
        c.setdefault("expect_doc_id", None)
        c.setdefault("query_type", "exact")
        c.setdefault("expect_answerable", True)
        c.setdefault("site_trap", False)
        c.setdefault("difficulty", "easy")
    return cases


def evaluate(retriever, cases, top_k=TOP_K):
    """返回每题的逐条结果 + 汇总指标。逐条结果是 A/B 配对检验的基础。"""
    per_query = []
    for case in cases:
        hits = retriever.search(case["q"], top_k=top_k)
        ids = [h.doc_id for h in hits]
        sites = [h.site_name for h in hits]
        top = hits[0] if hits else None
        want_doc = case["expect_doc_id"]
        answerable = case["expect_answerable"]

        if answerable:
            rank = ids.index(want_doc) + 1 if want_doc in ids else 0
            # 站点命中用子串匹配（兼容 "四六级考试网" 等全名）
            site_rank = 0
            if case.get("expect_site"):
                for i, s in enumerate(sites):
                    if case["expect_site"] in s:
                        site_rank = i + 1
                        break
        else:
            rank = 0  # 负例无正确文档
            site_rank = 0

        # 负例"拒答"代理指标：正确文档未出现在 Top-k（真实拒答需语义判断，见报告证据边界）
        neg_reject = (not answerable) and (want_doc not in ids)

        per_query.append({
            "q": case["q"],
            "query_type": case["query_type"],
            "difficulty": case["difficulty"],
            "site_trap": case["site_trap"],
            "answerable": answerable,
            "rank": rank,
            "site_rank": site_rank,
            "top1_score": round(float(top.score), 3) if top else 0.0,
            "top1_doc_id": top.doc_id if top else None,
            "top1_site": top.site_name if top else None,
            "neg_reject": neg_reject,
            "has_citation": bool(top and top.source_url.startswith("http")),
        })

    return per_query, summarize(per_query)


def summarize(per):
    pos = [p for p in per if p["answerable"]]
    neg = [p for p in per if not p["answerable"]]
    n_pos, n_neg = len(pos), len(neg)

    def rate(pred):
        return sum(1 for p in pos if pred(p)) / n_pos if n_pos else 0.0

    hit1 = rate(lambda p: p["rank"] == 1)
    hit3 = rate(lambda p: 0 < p["rank"] <= 3)
    hit5 = rate(lambda p: 0 < p["rank"] <= 5)
    mrr5 = sum(1 / p["rank"] for p in pos if 0 < p["rank"] <= 5) / n_pos if n_pos else 0.0
    site1 = rate(lambda p: p["site_rank"] == 1)
    # site_vs_doc_gap：站点命中减去文档命中，量化"站点对、文章错"被掩盖的程度
    site_vs_doc_gap = site1 - hit1

    trap = [p for p in pos if p["site_trap"]]
    trap_hit1 = sum(1 for p in trap if p["rank"] == 1) / len(trap) if trap else None

    return {
        "n_positive": n_pos,
        "n_negative": n_neg,
        "doc_hit@1": hit1,
        "doc_hit@3": hit3,
        "doc_hit@5": hit5,
        "mrr@5": mrr5,
        "site_hit@1": site1,  # 诊断，不作决策依据
        "site_vs_doc_gap": site_vs_doc_gap,
        "site_trap_doc_hit@1": trap_hit1,
        "neg_reject_proxy": (sum(1 for p in neg if p["neg_reject"]) / n_neg) if n_neg else None,
        "citation_rate": sum(1 for p in per if p["has_citation"]) / len(per),
    }


def bootstrap_ci(deltas, n_boot=2000, seed=42):
    """配对差值的 bootstrap 95% 置信区间。返回 (mean, lo, hi)。"""
    if not deltas:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(n_boot):
        means.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (
        sum(deltas) / n,
        means[int(0.025 * n_boot)],
        means[min(int(0.975 * n_boot), n_boot - 1)],
    )


def compare(a_per, b_per, label_a="A", label_b="B"):
    """逐题配对比较。指标用 rank 二值化（doc_hit@1）+ MRR 位置差。"""
    assert len(a_per) == len(b_per), "两套系统的题数必须一致"
    hit_deltas, changed = [], []
    for a, b in zip(a_per, b_per):
        assert a["q"] == b["q"], "golden set 题目顺序不一致"
        # 负例没有"正确文档"，rank 恒为 0，doc_hit@1 无定义；纳入 bootstrap
        # 只会用 5 个 delta=0 稀释效应量（20 题平均 +0.25 vs 正例平均 +0.333，
        # CI 下界被压到 0）。配对检验只在正例上做。
        if not a["answerable"]:
            continue
        d = (1 if b["rank"] == 1 else 0) - (1 if a["rank"] == 1 else 0)
        hit_deltas.append(d)
        if d != 0:
            changed.append((a["q"], d))

    mean, lo, hi = bootstrap_ci(hit_deltas)
    improved = sum(1 for d in hit_deltas if d > 0)
    regressed = sum(1 for d in hit_deltas if d < 0)

    print("\n=== A/B 配对比较（doc_hit@1，%s → %s）===" % (label_a, label_b))
    print(f"  改善题数: {improved} | 退化题数: {regressed} | 净变化: {improved - regressed}")
    print(f"  平均差值: {mean:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]")
    if lo > 0:
        verdict = "采纳：CI 下界 > 0，B 显著优于 A"
    elif hi < 0:
        verdict = "否决：CI 上界 < 0，B 显著差于 A"
    else:
        verdict = "不采纳：CI 跨 0，差异不显著（保持简单方案）"
    print(f"  判定: {verdict}")
    if changed:
        print("  变化题目:")
        for q, d in changed:
            print(f"    {'+B' if d > 0 else '-B'}  {q[:44]}")
    return verdict


def print_report(summ, per, label="系统"):
    print(f"\n=== {label} 汇总（正例 {summ['n_positive']} / 负例 {summ['n_negative']}）===")
    print("【主指标·文档级】")
    print(f"  doc_hit@1: {summ['doc_hit@1']:.0%}  |  doc_hit@3: {summ['doc_hit@3']:.0%}  |  doc_hit@5: {summ['doc_hit@5']:.0%}")
    print(f"  mrr@5:     {summ['mrr@5']:.3f}")
    print(f"  site_trap 题 doc_hit@1: {summ['site_trap_doc_hit@1']:.0%}" if summ['site_trap_doc_hit@1'] is not None else "  site_trap: 无")
    print("【诊断·站点级（不作决策依据）】")
    print(f"  site_hit@1:       {summ['site_hit@1']:.0%}")
    print(f"  site_vs_doc_gap:  {summ['site_vs_doc_gap']:+.0%}  ← 站点指标掩盖的'同站不同篇'错误")
    print("【负例】")
    reject = summ['neg_reject_proxy']
    print(f"  neg_reject_proxy: {reject:.0%}" if reject is not None else "  neg_reject: 无负例")
    print("  ⚠ 代理指标=正确文档未进Top-k；真实拒答需语义判断（分数正负例重叠，见报告）")
    print(f"  出处可追溯率: {summ['citation_rate']:.0%}")

    print("\n【逐题明细】")
    for p in per:
        if p["answerable"]:
            flag = "OK" if p["rank"] == 1 else ("R3" if 0 < p["rank"] <= 3 else "MISS")
            extra = " [TRAP]" if p["site_trap"] else ""
            print(f"  [{flag:4}] rank={p['rank'] or '-':>2} score={p['top1_score']:>6} {p['query_type']:<15}{extra} {p['q'][:34]}")
        else:
            print(f"  [NEG ] reject={'Y' if p['neg_reject'] else 'N'} score={p['top1_score']:>6} top1={p['top1_doc_id'] or '-'} {p['q'][:34]}")


def main():
    ap = argparse.ArgumentParser(description="检索评估 v2（文档级 + 配对检验）")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                    help="比较两份 per_query JSON（由 --dump 产出）")
    ap.add_argument("--dump", metavar="OUT.json", help="把逐题结果存为 JSON 供 A/B 比较")
    args = ap.parse_args()

    cases = load_cases()
    r = BM25Retriever()
    print(f"索引文档数: {len(r.docs)} | 评估问题数: {len(cases)}")

    per, summ = evaluate(r, cases)
    print_report(summ, per, "BM25 基线")
    if args.dump:
        Path(args.dump).write_text(json.dumps(per, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n逐题结果已存: {args.dump}")

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        compare(a, b, "A", "B")

    print("\n注：站点命中率仅作诊断；决策看文档级指标 + 配对 CI。CI 跨 0 就不加复杂度。")


if __name__ == "__main__":
    main()

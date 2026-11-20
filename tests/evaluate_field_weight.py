r"""阶段2追加实验：BM25 字段加权 vs 基线，用同一 20 题做配对检验。

运行：
  .venv\Scripts\python.exe tests\evaluate_field_weight.py

判定规则（沿用阶段2报告）：只有配对 bootstrap 95%CI 下界 > 0 才接入主链路；
CI 跨 0 就不加复杂度。敏感性分析的 alpha 只作探索，不参与采纳判定。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from evaluate import bootstrap_ci, compare, evaluate, load_cases, print_report  # noqa: E402
from field_weighted import FieldWeightedRetriever  # noqa: E402

BASELINE = ROOT / "data" / "bm25_baseline.json"
DUMP = ROOT / "data" / "field_weighted_alpha03.json"
ALPHAS = [0.0, 0.2, 0.3, 0.5, 0.7]
PRIMARY_ALPHA = 0.3


def loocv_alpha(cases, base_per, alphas):
    """留一交叉验证选 alpha：每题用其余正例挑 alpha，再在该题上评估。

    目的：敏感性扫描里若某个 alpha 在 20 题上冲高，不能直接采纳（在同一批题上
    调参再在同一批题上报告 = 过拟合）。LOOCV 让「选 alpha」与「评估该题」解耦，
    得到的命中率是无偏估计。平局取更小 alpha（更保守、更接近纯 BM25）。
    """
    pos = [c for c in cases if c["expect_answerable"]]
    base_map = {p["q"]: p for p in base_per}
    retrievers = {a: FieldWeightedRetriever(alpha=a) for a in alphas}
    hit = {}
    for a, r in retrievers.items():
        per, _ = evaluate(r, cases)
        hit[a] = {p["q"]: (1 if p["rank"] == 1 else 0) for p in per}

    selected, hits, chosen_count = [], [], {a: 0 for a in alphas}
    for i, c in enumerate(pos):
        train_score = {a: sum(hit[a][o["q"]] for j, o in enumerate(pos) if j != i) for a in alphas}
        best = max(alphas, key=lambda a: (train_score[a], -a))
        chosen_count[best] += 1
        h = hit[best][c["q"]]
        hits.append(h)
        b = 1 if base_map[c["q"]]["rank"] == 1 else 0
        selected.append(h - b)

    mean, lo, hi = bootstrap_ci(selected)
    loocv_rate = sum(hits) / len(hits)
    print("\n=== LOOCV 无偏复核（在其余正例上选 alpha，评估留出的那一题）===")
    print(f"  alpha 被选中次数: {chosen_count}")
    print(f"  LOOCV doc_hit@1: {loocv_rate:.0%}  ({sum(hits)}/{len(hits)})  | 基线: "
          f"{sum(1 for c in pos if base_map[c['q']]['rank'] == 1) / len(pos):.0%}")
    print(f"  逐题差值平均 {mean:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]")
    verdict = "显著优于基线（无偏复核）" if lo > 0 else (
        "显著差于基线" if hi < 0 else "不显著：CI 跨 0，维持基线")
    print(f"  判定: {verdict}")
    return verdict


def main():
    cases = load_cases()
    base_per = json.loads(BASELINE.read_text(encoding="utf-8"))
    base_map = {p["q"]: p for p in base_per}

    # ---- 主判定：alpha=0.3 ----
    r = FieldWeightedRetriever(alpha=PRIMARY_ALPHA)
    per, summ = evaluate(r, cases)
    print_report(summ, per, f"字段加权 alpha={PRIMARY_ALPHA}")
    DUMP.write_text(json.dumps(per, ensure_ascii=False, indent=2), encoding="utf-8")
    compare(base_per, per, "BM25基线(标题隐式2x)", f"字段加权 alpha={PRIMARY_ALPHA}")

    # ---- 敏感性分析：其他 alpha（探索性，不作采纳依据）----
    print("\n=== 敏感性分析（探索性；alpha 在 20 题上扫描，不作采纳依据）===")
    print("  alpha | doc_hit@1 | mrr@5  | 改善/退化 | CI 下界 上界")
    for a in ALPHAS:
        ra = FieldWeightedRetriever(alpha=a)
        pa, sa = evaluate(ra, cases)
        deltas = []
        for c, p in zip(cases, pa):
            if not c["expect_answerable"]:
                continue
            b = 1 if base_map[c["q"]]["rank"] == 1 else 0
            n = 1 if p["rank"] == 1 else 0
            deltas.append(n - b)
        m, lo, hi = bootstrap_ci(deltas)
        imp = sum(1 for d in deltas if d > 0)
        reg = sum(1 for d in deltas if d < 0)
        mark = "  <-- 主判定" if a == PRIMARY_ALPHA else ""
        print(f"  {a:>5} | {sa['doc_hit@1']:>9.0%} | {sa['mrr@5']:>6.3f} | "
              f"{imp:>3}/{reg:<3}     | {lo:+.3f} {hi:+.3f}{mark}")

    # ---- 无偏复核：LOOCV 选 alpha，避免在同一批题上扫参又在同一批题上报收益 ----
    loocv_verdict = loocv_alpha(cases, base_per, ALPHAS)

    print(f"\n逐题结果已存: {DUMP}")
    print("最终判定口径：主判定 alpha=0.3 不显著；但敏感性扫描发现 alpha=0.5 冲高，")
    print("必须过 LOOCV 无偏复核（选参与评估解耦）才允许接入主链路。")
    print(f"LOOCV 结论: {loocv_verdict}")


if __name__ == "__main__":
    main()

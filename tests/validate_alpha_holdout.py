r"""用独立 holdout 题集复核 alpha=0.5（2026-11-20）。

独立性：alpha=0.5 是在原 20 题上经 LOOCV 选出的；本脚本用全新的 18 题
（tests/holdout_set.json，未参与选参）验证该选择是否泛化。这是比 LOOCV
更强的一层验证——LOOCV 仍在同一批题里打转，holdout 是完全没见过的题。

判定：
1. alpha=0.5 在 holdout 上的 doc_hit@1 是否仍显著优于其他 alpha（尤其 0.3/0.7）；
2. 用 alpha=0.5 固定，扫所有 alpha 在 holdout 上的表现，看 0.5 是否仍在最优区间；
3. 负例拒答是否保持。

运行：.venv\Scripts\python.exe tests\validate_alpha_holdout.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from evaluate import bootstrap_ci, evaluate, load_cases, print_report  # noqa: E402
from field_weighted import FieldWeightedRetriever  # noqa: E402

HOLDOUT = Path(__file__).resolve().parent / "holdout_set.json"
ALPHAS = [0.0, 0.2, 0.3, 0.5, 0.7, 1.0]


def load_holdout():
    cases = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    for c in cases:
        c.setdefault("expect_doc_id", None)
        c.setdefault("query_type", "exact")
        c.setdefault("expect_answerable", True)
        c.setdefault("site_trap", False)
        c.setdefault("difficulty", "easy")
    return cases


def main():
    cases = load_holdout()
    print(f"holdout 题数: {len(cases)} | 类型: {dict(Counter(c['query_type'] for c in cases))}")

    # 各 alpha 在 holdout 上的完整评估
    results = {}
    for a in ALPHAS:
        r = FieldWeightedRetriever(alpha=a)
        per, summ = evaluate(r, cases)
        results[a] = (per, summ)

    print("\n=== 各 alpha 在独立 holdout 上的表现（正例 15 / 负例 3）===")
    print("  alpha | doc_hit@1 | doc_hit@3 |  mrr@5  | site_trap@1 | neg_reject")
    for a in ALPHAS:
        s = results[a][1]
        mark = "  <-- 已接入" if a == 0.5 else ""
        print(f"  {a:>5} | {s['doc_hit@1']:>9.0%} | {s['doc_hit@3']:>9.0%} | "
              f"{s['mrr@5']:>7.3f} | {str(s['site_trap_doc_hit@1']):>10} | "
              f"{s['neg_reject_proxy']:>6.0%}{mark}")

    # alpha=0.5 vs 各对照的配对检验（只在正例上，与修好的 evaluate 口径一致）
    per05 = results[0.5][0]
    print("\n=== 配对检验：alpha=0.5 vs 其他（doc_hit@1，只在正例上）===")
    for a in ALPHAS:
        if a == 0.5:
            continue
        pa = results[a][0]
        deltas = [(1 if p05["rank"] == 1 else 0) - (1 if pa["rank"] == 1 else 0)
                  for p05, pa in zip(per05, pa) if p05["answerable"]]
        m, lo, hi = bootstrap_ci(deltas)
        verdict = "0.5 显著优" if lo > 0 else ("0.5 显著差" if hi < 0 else "不显著")
        print(f"  0.5 vs {a}: 平均 {m:+.3f} 95%CI [{lo:+.3f},{hi:+.3f}] -> {verdict}")
    print("  注：15 正例小样本下，1 题差异=6.7%，bootstrap 下界易压到 0；"
          "此处只看方向一致性，显著性以原 20 题 LOOCV 为准。")

    # 主判定：0.5 是否仍在最优区间（对比次优 alpha 的加权平均）
    best_a = max(ALPHAS, key=lambda a: results[a][1]["doc_hit@1"])
    s05 = results[0.5][1]
    print(f"\n=== 主判定 ===")
    print(f"  holdout 上 doc_hit@1 最高的 alpha = {best_a}（{results[best_a][1]['doc_hit@1']:.0%}）")
    print(f"  已接入 alpha=0.5 在 holdout 上 = {s05['doc_hit@1']:.0%}")
    if best_a == 0.5:
        print("  结论: ✅ holdout 独立复核通过——0.5 在没参与选参的新题上仍是最优")
    elif s05["doc_hit@1"] >= results[best_a][1]["doc_hit@1"] - 0.05:
        print("  结论: ✅ 基本通过——0.5 与最优值差距 <5 个百分点，无需改")
    else:
        print(f"  结论: ⚠️ 需要调整——holdout 显示 alpha={best_a} 明显更优，建议复核")

    print_report(s05, per05, "已接入 alpha=0.5（holdout）")

    out = ROOT / "data" / "holdout_alpha_validation.json"
    out.write_text(json.dumps({
        "holdout_n": len(cases),
        "per_alpha": {str(a): results[a][1] for a in ALPHAS},
        "best_alpha_on_holdout": best_a,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n留痕: {out}")


if __name__ == "__main__":
    main()

r"""方案 B 新文档 holdout：反向泄漏检查 + alpha"确证不更差"（2026-11-24）。

背景：阶段 2 的 tests/holdout_set.json（18 题）是旧 22 篇的独立验证集，
不能用来验证新增省级公告。本脚本读 tests/holdout_expansion.json（45-50 题，
expect_doc_id 全部指向新入库文档），做两件事：

1. 反向泄漏检查：用入库前的旧库快照 data/clean_baseline/ 检索每道新题；
   若旧库文档已能覆盖题目的 expect_keywords（>=60%），说明该题不是新文档
   独有的，作废重写。保证 holdout 真正检验新文档。
2. alpha 复核：定位是"确证不更差"，不是第三次选参——若 alpha=0.5 与最优值
   差距 <5 个百分点且负例拒答不掉，即保持 0.5，不在测试集上重新挑参。

运行：
  python tests/validate_expansion_holdout.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from evaluate import bootstrap_ci, evaluate, print_report  # noqa: E402
from field_weighted import FieldWeightedRetriever  # noqa: E402
from retrieve import BM25Retriever  # noqa: E402

HOLDOUT = Path(__file__).resolve().parent / "holdout_expansion.json"
BASELINE = ROOT / "data" / "clean_baseline"
REPORT = ROOT / "data" / "expansion_holdout_validation.json"
ALPHAS = [0.0, 0.3, 0.5, 0.7, 1.0]
LEAK_THRESHOLD = 0.6


def load_cases():
    cases = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    for c in cases:
        c.setdefault("expect_doc_id", None)
        c.setdefault("query_type", "exact")
        c.setdefault("expect_answerable", True)
        c.setdefault("site_trap", False)
        c.setdefault("difficulty", "easy")
        c.setdefault("expect_keywords", [])
    return cases


def leak_check(cases):
    """旧库快照能答的题作废；返回 (有效题, 泄漏题明细)。"""
    if not BASELINE.exists():
        print(f"旧库快照不存在：{BASELINE}（入库前需先备份 data/clean）")
        return cases, []
    old = BM25Retriever(data_dir=BASELINE, alpha=0.5)
    valid, leaked = [], []
    for c in cases:
        if not c["expect_answerable"]:
            valid.append(c)
            continue
        kws = c.get("expect_keywords", [])
        covered = False
        detail = None
        for h in old.search(c["q"], top_k=3):
            ratio = sum(1 for k in kws if k in h.markdown) / len(kws) if kws else 0
            if ratio >= LEAK_THRESHOLD:
                covered = True
                detail = {"q": c["q"], "old_doc": h.doc_id, "old_title": h.title,
                          "coverage": round(ratio, 2)}
                break
        if covered:
            leaked.append(detail)
        else:
            valid.append(c)
    return valid, leaked


def main():
    cases = load_cases()
    print(f"新 holdout 原题数 {len(cases)} | 类型 {dict(Counter(c['query_type'] for c in cases))}")
    valid, leaked = leak_check(cases)
    print(f"反向泄漏检查：有效 {len(valid)} 题，作废 {len(leaked)} 题")
    for d in leaked:
        print(f"  泄漏 {d['coverage']:.0%} | {d['q'][:44]} → 旧库 {d['old_title'][:36]}")

    results = {}
    for a in ALPHAS:
        r = FieldWeightedRetriever(alpha=a)
        results[a] = evaluate(r, valid)

    print("\n=== 新文档 holdout 各 alpha 表现 ===")
    print("  alpha | doc_hit@1 | doc_hit@3 |  mrr@5  | neg_reject")
    for a in ALPHAS:
        s = results[a][1]
        mark = "  <-- 已接入" if a == 0.5 else ""
        print(f"  {a:>5} | {s['doc_hit@1']:>9.0%} | {s['doc_hit@3']:>9.0%} | "
              f"{s['mrr@5']:>7.3f} | {s['neg_reject_proxy']:>6.0%}{mark}")

    best_a = max(ALPHAS, key=lambda a: results[a][1]["doc_hit@1"])
    s05 = results[0.5][1]
    gap = results[best_a][1]["doc_hit@1"] - s05["doc_hit@1"]
    neg_ok = s05["neg_reject_proxy"] >= results[best_a][1]["neg_reject_proxy"] - 0.01
    confirmed = gap < 0.05 and neg_ok

    per05 = results[0.5][0]
    print("\n=== 配对检验 alpha=0.5 vs 其余（只正例）===")
    for a in ALPHAS:
        if a == 0.5:
            continue
        deltas = [(1 if p["rank"] == 1 else 0) - (1 if q["rank"] == 1 else 0)
                  for p, q in zip(per05, results[a][0]) if p["answerable"]]
        m, lo, hi = bootstrap_ci(deltas)
        print(f"  0.5 vs {a}: 平均 {m:+.3f} 95%CI [{lo:+.3f},{hi:+.3f}]")

    print(f"\n=== 主判定（确证不更差）===")
    print(f"  新题最优 alpha={best_a}（{results[best_a][1]['doc_hit@1']:.0%}），"
          f"已接入 0.5={s05['doc_hit@1']:.0%}，差距 {gap:.0%}")
    print(f"  结论：{'✅ 保持 alpha=0.5（不更差）' if confirmed else '⚠️ 需复核（0.5 明显更差或负例退化）'}")
    print_report(s05, per05, "已接入 alpha=0.5（新文档 holdout）")

    out = {
        "note": "新文档 holdout：反向泄漏检查 + alpha 确证不更差（非第三次选参）",
        "original_n": len(cases), "valid_n": len(valid), "leaked": leaked,
        "per_alpha": {str(a): results[a][1] for a in ALPHAS},
        "best_alpha_on_new_holdout": best_a, "gap_to_best": gap,
        "confirmed": confirmed,
    }
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n留痕：{REPORT}")


if __name__ == "__main__":
    main()

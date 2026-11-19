r"""检索评估：golden set 跑 recall@k + 站点命中率 + 出处可追溯性。
运行：.venv\Scripts\python.exe tests\evaluate.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from retrieve import BM25Retriever  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden_set.json"


def main():
    r = BM25Retriever()
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    print(f"索引文档数: {len(r.docs)} | 评估问题数: {len(cases)}\n")

    hit1 = hit3 = site_ok = cite_ok = 0
    for case in cases:
        hits = r.search(case["q"], top_k=3)
        top = hits[0] if hits else None
        r1 = bool(top and case["expect_site"] in top.site_name)
        r3 = any(case["expect_site"] in h.site_name for h in hits)
        kw_hit = sum(1 for k in case["expect_keywords"] if k in (top.markdown if top else ""))
        site_ok += r1
        hit1 += r1
        hit3 += r3
        cite_ok += bool(top and top.source_url.startswith("http"))

        status = "PASS" if r1 else ("WARN" if r3 else "FAIL")
        print(f"[{status}] {case['q'][:40]}")
        if hits:
            print(f"       期望站点={case['expect_site']} | Top1={hits[0].site_name} ({hits[0].score}) | 关键词命中={kw_hit}/{len(case['expect_keywords'])}")
            print(f"       出处: {hits[0].source_url}")
        else:
            print("       无检索结果")
        print()

    n = len(cases)
    print("=== 汇总 ===")
    print(f"Top-1 站点命中率: {hit1}/{n} = {hit1/n:.0%}")
    print(f"Top-3 站点命中率: {hit3}/{n} = {hit3/n:.0%}")
    print(f"出处可追溯率:     {cite_ok}/{n} = {cite_ok/n:.0%}")
    print("\n注：MVP 阶段先验检索（P0-5），recall@k 过关后再评估 embedding 与生成。")


if __name__ == "__main__":
    main()

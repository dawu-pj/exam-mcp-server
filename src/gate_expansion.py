r"""方案 B 入库门禁（2026-11-24）：候选文档先模拟，达标才准入库。

依据：《受控扩量调查与实施方案》第五节第 3 步——
  「抓完先跑模拟（临时目录建索引），若 doc_hit@1 掉到 <80% 就不入库，退回方案 C；
    ≥80% 才正式入库。」

做法：
  1. 读 data/expansion_candidates/（collect_provincial.py 产物，gate=pending）
  2. 与 data/clean/ 合并到临时目录 data/expansion_gate/
  3. 用现有 20 题 golden set 对比扩量前后 doc_hit@1/@3
  4. 检查 2 道回归哨兵题（"正式立项名单"）是否仍在 Top-3
  5. 负例 Top-1 单独列出（新站若真的带来教师资格等公告，旧负例标签需人工复核）
  6. 写 data/expansion_gate_report.json；PASS 才允许执行 promote 步骤

本脚本不写 data/clean、不改主链路；只在临时目录建索引。
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from retrieve import BM25Retriever  # noqa: E402

CLEAN_DIR = ROOT / "data" / "clean"
CAND_DIR = ROOT / "data" / "expansion_candidates"
GATE_DIR = ROOT / "data" / "expansion_gate"
REPORT = ROOT / "data" / "expansion_gate_report.json"
THRESHOLD = 0.80
ALPHA = 0.5


def candidate_docs():
    docs = []
    for meta_path in sorted(CAND_DIR.glob("*.meta.json")):
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        if m.get("gate") != "pending":
            continue
        md = meta_path.with_suffix("").with_suffix(".md")
        if md.exists():
            docs.append((m, md))
    return docs


def build_merged(docs):
    if GATE_DIR.exists():
        shutil.rmtree(GATE_DIR)
    GATE_DIR.mkdir(parents=True)
    for d in (CLEAN_DIR,):
        for p in d.glob("*.md"):
            shutil.copy(p, GATE_DIR / p.name)
        for p in d.glob("*.meta.json"):
            shutil.copy(p, GATE_DIR / p.name)
    for m, md in docs:
        shutil.copy(md, GATE_DIR / md.name)
        (GATE_DIR / f"{m['doc_id']}.meta.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return GATE_DIR


def rank_of(retriever, q, want, k=10):
    hits = retriever.search(q, top_k=k)
    return next((i + 1 for i, h in enumerate(hits) if h.doc_id == want), None)


def main():
    docs = candidate_docs()
    print(f"=== 方案 B 入库门禁 ===")
    print(f"候选文档 {len(docs)} 篇（gate=pending）")
    if not docs:
        print("无候选，门禁不通过：请先跑 collect_provincial.py")
        return

    merged = build_merged(docs)
    before = BM25Retriever(data_dir=CLEAN_DIR, alpha=ALPHA)
    after = BM25Retriever(data_dir=merged, alpha=ALPHA)
    print(f"扩量前 {len(before.docs)} 篇 → 扩量后 {len(after.docs)} 篇（alpha={ALPHA}）")

    cases = json.loads((ROOT / "tests" / "golden_set.json").read_text(encoding="utf-8"))
    rows, changed, negatives = [], [], []
    for c in cases:
        if not c["expect_answerable"]:
            hits = after.search(c["q"], top_k=3)
            negatives.append({
                "q": c["q"],
                "top1": {"doc_id": hits[0].doc_id, "title": hits[0].title, "score": hits[0].score}
                if hits else None,
                "note": "neg_reject_proxy 恒真（负例无正确文档）；Top-1 命中是 evaluate.py 已注明的语义局限，非门禁指标",
            })
            continue
        want = c["expect_doc_id"]
        b = rank_of(before, c["q"], want)
        a = rank_of(after, c["q"], want)
        rows.append({"q": c["q"], "expect_doc_id": want, "before": b, "after": a})
        if b != a:
            changed.append({"q": c["q"], "before": b, "after": a})

    def hit_at(key, k):
        return sum(1 for r in rows if r[key] and r[key] <= k) / len(rows)

    sentinels = [r for r in rows if "正式立项名单" in r["q"]]
    sentinel_ok = all(r["after"] and r["after"] <= 3 for r in sentinels)
    after_at1 = hit_at("after", 1)
    passed = after_at1 >= THRESHOLD and sentinel_ok

    report = {
        "gate_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "方案B入库门禁：doc_hit@1>=80% 且回归哨兵 Top-3 才准入库；未写 data/clean",
        "threshold": THRESHOLD, "alpha": ALPHA,
        "before_docs": len(before.docs), "after_docs": len(after.docs), "added": len(docs),
        "before_doc_hit@1": hit_at("before", 1), "before_doc_hit@3": hit_at("before", 3),
        "after_doc_hit@1": after_at1, "after_doc_hit@3": hit_at("after", 3),
        "changed": changed, "sentinels": sentinels, "sentinel_ok": sentinel_ok,
        "negatives": negatives, "decision": "PASS" if passed else "FAIL",
        "reason": (
            "doc_hit@1 达标且哨兵未掉出 Top-3，可入库"
            if passed else
            f"doc_hit@1={after_at1:.0%} < {THRESHOLD:.0%} 或哨兵掉出 Top-3，不入库，退回方案 C"
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 结果 ===")
    print(f"doc_hit@1: {report['before_doc_hit@1']:.0%} → {report['after_doc_hit@1']:.0%}（门槛 {THRESHOLD:.0%}）")
    print(f"doc_hit@3: {report['before_doc_hit@3']:.0%} → {report['after_doc_hit@3']:.0%}")
    print(f"排名变化 {len(changed)}/{len(rows)} 题；哨兵 {'OK' if sentinel_ok else '掉出 Top-3'}")
    for r in changed:
        print(f"  rank {r['before']}→{r['after']} | {r['q'][:46]}")
    # 负例说明：tests/evaluate.py 的 neg_reject_proxy = "正确文档未进 Top-k"，
    # 负例没有正确文档故该代理恒真；Top-1 有命中是已知的语义局限（见 evaluate.py
    # 第 180 行），不是本次扩量引入的退化。这里只提示语义观察，不改门禁判定。
    if negatives:
        print(f"负例语义观察（{len(negatives)} 题，neg_reject_proxy 恒真、不影响门禁）：")
        for n in negatives:
            top = n["top1"]
            print(f"  {n['q'][:38]} → {top['title'][:36] if top else '拒答'}")
    print(f"\n门禁判定：{report['decision']} —— {report['reason']}")
    print(f"留痕：{REPORT}")


if __name__ == "__main__":
    main()

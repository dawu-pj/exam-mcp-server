r"""扩量影响模拟实验（2026-11-23，调查阶段，不入库不改主链路）。

目的：回答"18→50 篇会不会伤到现有检索"——用实证代替理论推演。
做法：
  1. 从 column_probe.json 抽 10 篇新候选（标题确认、非导航、正文≥500 字），抓正文到临时目录 data/expansion_sim/
  2. 用 BM25Retriever(data_dir=临时目录) 把 18 旧 + 10 新合并建索引
  3. 跑现有 20 题 golden set，对比"扩量前 vs 扩量后"的 doc_hit@1/@3、逐题排名变化
  4. 重点看 2 道回归哨兵题是否掉出 Top-3

合规：公开静态页、明示 UA、每域 1 req/s、GET、不绕 WAF、临时留痕不动 data/clean。
实验产物：data/expansion_sim/（临时）+ data/expansion_sim_report.json
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

import requests
import trafilatura

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "crawler"))
from retrieve import BM25Retriever
from cleaning import content_fingerprint

SIM_DIR = ROOT / "data" / "expansion_sim"
CLEAN_DIR = ROOT / "data" / "clean"
REPORT = ROOT / "data" / "expansion_sim_report.json"
UA = "Mozilla/5.0 (compatible; ExamIntelBot/1.0; public exam-info; research)"
NAV = re.compile(r"^(考试资讯|公示公告|通知公告|新闻资讯|考试动态|首页|更多|ENGLISH)$")
TARGET_N = 10
MIN_CHARS = 500


def pick_candidates(n):
    """从 title 确认候选里挑：非导航、研招网优先（教育部政策，最同质）。"""
    report = json.loads((ROOT / "data" / "column_probe.json").read_text(encoding="utf-8"))
    yz = [s for s in report["sites"] if s["site"] == "研招网"][0]
    picked, seen = [], set()
    for c in yz["new_candidates"]:
        if c["looks"] != "title":
            continue
        t = c["title"].strip()
        if NAV.match(t) or t in seen:
            continue
        seen.add(t)
        picked.append({"title": t, "url": c["url"], "site_name": "研招网"})
        if len(picked) >= n:
            break
    return picked


def fetch_articles(cands):
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    existing_fp = {content_fingerprint(p.read_text(encoding="utf-8"))
                   for p in CLEAN_DIR.glob("*.md")}
    saved, skipped = [], []
    for i, c in enumerate(cands):
        try:
            r = requests.get(c["url"], headers={"User-Agent": UA}, timeout=20)
            r.encoding = r.apparent_encoding
            txt = trafilatura.extract(r.text) or ""
            if len(txt) < MIN_CHARS:
                skipped.append({"title": c["title"], "reason": f"正文仅 {len(txt)} 字"})
                continue
            fp = content_fingerprint(txt)
            if fp in existing_fp:
                skipped.append({"title": c["title"], "reason": "与现有文档正文重复"})
                continue
            doc_id = f"sim{i:03d}" + fp[:8]
            (SIM_DIR / f"{doc_id}.md").write_text(txt, encoding="utf-8")
            meta = {"doc_id": doc_id, "title": c["title"], "source_url": c["url"],
                    "site_name": c["site_name"], "text_chars": len(txt)}
            (SIM_DIR / f"{doc_id}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            saved.append(meta)
            print(f"  收录 {doc_id[:12]} | {len(txt)} 字 | {c['title'][:38]}")
        except Exception as e:
            skipped.append({"title": c["title"], "reason": f"{type(e).__name__}: {str(e)[:60]}"})
        time.sleep(1)
    return saved, skipped


def merge_dirs():
    """把现有 clean + 临时 sim 复制到一个合并目录供建索引。"""
    merged = ROOT / "data" / "expansion_sim" / "_merged"
    if merged.exists():
        shutil.rmtree(merged)
    merged.mkdir(parents=True)
    for d in (CLEAN_DIR, SIM_DIR):
        for p in d.glob("*.md"):
            shutil.copy(p, merged / p.name)
        for p in d.glob("*.meta.json"):
            shutil.copy(p, merged / p.name)
    return merged


def main():
    print("=== 1. 挑候选 ===")
    cands = pick_candidates(TARGET_N + 4)
    print(f"挑到 {len(cands)} 个候选，抓正文（≥{MIN_CHARS}字）")
    saved, skipped = fetch_articles(cands)
    print(f"收录 {len(saved)} 篇，跳过 {len(skipped)} 篇")
    if not saved:
        print("无新文档，实验取消")
        return

    print("\n=== 2. 建索引对比 ===")
    merged = merge_dirs()
    before = BM25Retriever(data_dir=CLEAN_DIR, alpha=0.5)
    after = BM25Retriever(data_dir=merged, alpha=0.5)
    print(f"扩量前 {len(before.docs)} 篇 → 扩量后 {len(after.docs)} 篇")

    cases = json.loads((ROOT / "tests" / "golden_set.json").read_text(encoding="utf-8"))
    rows, changed = [], []
    for c in cases:
        if not c["expect_answerable"]:
            continue
        want = c["expect_doc_id"]
        b_rank = next((i + 1 for i, h in enumerate(before.search(c["q"], top_k=10))
                       if h.doc_id == want), None)
        a_hits = after.search(c["q"], top_k=10)
        a_rank = next((i + 1 for i, h in enumerate(a_hits) if h.doc_id == want), None)
        rows.append({"q": c["q"], "before": b_rank, "after": a_rank})
        if b_rank != a_rank:
            changed.append((b_rank, a_rank, c["q"]))

    def hit_at(rows, k):
        return sum(1 for r in rows if r["after"] and r["after"] <= k) / len(rows)

    summary = {
        "old_docs": len(before.docs), "new_docs": len(after.docs),
        "added": len(saved), "skipped": skipped,
        "before_doc_hit@1": sum(1 for r in rows if r["before"] == 1) / len(rows),
        "after_doc_hit@1": hit_at(rows, 1),
        "after_doc_hit@3": hit_at(rows, 3),
        "changed": [{"before": b, "after": a, "q": q} for b, a, q in changed],
        "sentinels": [r for r in rows if "正式立项名单" in r["q"]],
    }
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 3. 结果 ===")
    print(f"doc_hit@1: 扩量前 {summary['before_doc_hit@1']:.0%} → 扩量后 {summary['after_doc_hit@1']:.0%}")
    print(f"doc_hit@3 (扩量后): {summary['after_doc_hit@3']:.0%}")
    print(f"排名变化的题: {len(changed)}/{len(rows)}")
    for b, a, q in changed:
        print(f"  rank {b}→{a} | {q[:45]}")
    print("\n回归哨兵题:")
    for r in summary["sentinels"]:
        print(f"  rank {r['before']}→{r['after']} | {r['q'][:45]}")
    print(f"\n留痕: {REPORT}")


if __name__ == "__main__":
    main()

r"""方案 B 正式入库（2026-11-24）：只在门禁 PASS 后把候选复制进 data/clean。

安全设计：
  - 默认 dry-run，只列出将入库的文档；必须显式 --apply 才真正写入
  - 强制校验 data/expansion_gate_report.json 的 decision == PASS
  - 二次校验正文指纹与现有库不冲突、gate 字段为 pending
  - 写审计留痕 data/promotion_report.json（含 doc_id / URL / 字数 / 门禁分数）

用法：
  python src/promote_expansion.py           # dry-run
  python src/promote_expansion.py --apply   # 真正入库
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "crawler"))
from cleaning import content_fingerprint, normalize_url  # noqa: E402

CAND_DIR = ROOT / "data" / "expansion_candidates"
CLEAN_DIR = ROOT / "data" / "clean"
GATE_REPORT = ROOT / "data" / "expansion_gate_report.json"
PROMOTION_REPORT = ROOT / "data" / "promotion_report.json"


def main():
    apply = "--apply" in sys.argv[1:]
    if not GATE_REPORT.exists():
        print("门禁报告不存在，禁止入库。先跑 src/gate_expansion.py")
        return
    gate = json.loads(GATE_REPORT.read_text(encoding="utf-8"))
    if gate.get("decision") != "PASS":
        print(f"门禁判定 {gate.get('decision')}，禁止入库。原因：{gate.get('reason')}")
        return

    cands = []
    for meta_path in sorted(CAND_DIR.glob("*.meta.json")):
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        if m.get("gate") == "pending":
            cands.append((m, meta_path.with_suffix("").with_suffix(".md")))
    if not cands:
        print("没有 gate=pending 的候选文档，无需入库")
        return

    existing_urls, existing_fps = set(), set()
    for p in CLEAN_DIR.glob("*.meta.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        existing_urls.add(normalize_url(m["source_url"]))
        existing_fps.add(m.get("content_sha256") or content_fingerprint(
            p.with_suffix("").with_suffix(".md").read_text(encoding="utf-8")))

    to_write, rejected = [], []
    for m, md in cands:
        url = normalize_url(m["source_url"])
        text = md.read_text(encoding="utf-8")
        fp = content_fingerprint(text)
        if fp != m.get("content_sha256"):
            rejected.append({"doc_id": m["doc_id"], "reason": "正文指纹与meta不一致"})
        elif url in existing_urls or fp in existing_fps:
            rejected.append({"doc_id": m["doc_id"], "reason": "URL/指纹与现有库冲突"})
        else:
            to_write.append((m, md))
            existing_urls.add(url)
            existing_fps.add(fp)

    print(f"门禁 PASS：doc_hit@1 {gate['after_doc_hit@1']:.0%}（门槛 {gate['threshold']:.0%}）")
    print(f"待入库 {len(to_write)} 篇，二次校验拒绝 {len(rejected)} 篇")
    for m, _ in to_write:
        print(f"  {m['doc_id'][:12]} | {m['site_name']} | {m['text_chars']} 字 | {m['title'][:40]}")
    for r in rejected:
        print(f"  拒绝 {r['doc_id'][:12]}：{r['reason']}")

    if not apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply 执行入库。")
        return

    for m, md in to_write:
        m["gate"] = "promoted"
        m["promoted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        shutil.copy(md, CLEAN_DIR / md.name)
        (CLEAN_DIR / f"{m['doc_id']}.meta.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        cand_meta = CAND_DIR / f"{m['doc_id']}.meta.json"
        cand_meta.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "promoted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gate_report": str(GATE_REPORT.relative_to(ROOT)),
        "gate_doc_hit@1": gate["after_doc_hit@1"],
        "gate_doc_hit@3": gate["after_doc_hit@3"],
        "promoted": [{"doc_id": m["doc_id"], "title": m["title"], "site_name": m["site_name"],
                      "source_url": m["source_url"], "text_chars": m["text_chars"]}
                     for m, _ in to_write],
        "rejected": rejected,
    }
    PROMOTION_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已入库 {len(to_write)} 篇 → {CLEAN_DIR}")
    print(f"审计留痕：{PROMOTION_REPORT}")


if __name__ == "__main__":
    main()

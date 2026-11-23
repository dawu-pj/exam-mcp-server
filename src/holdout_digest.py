r"""出题参考摘要：从候选文档抽标题 + 正文开头 + 含数字/日期的关键行。

只输出 data/expansion_digest.md（UTF-8），供人工构造 holdout 题时核对事实，
不做任何网络请求、不入库。出题必须基于正文事实，不能凭标题猜。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAND_DIR = ROOT / "data" / "expansion_candidates"
OUT = ROOT / "data" / "expansion_digest.md"


def main():
    lines_out = ["# 扩量候选文档摘要（出题参考）", ""]
    metas = sorted(CAND_DIR.glob("*.meta.json"))
    for i, p in enumerate(metas, 1):
        m = json.loads(p.read_text(encoding="utf-8"))
        md = p.with_suffix("").with_suffix(".md")
        text = md.read_text(encoding="utf-8")
        parts = [x.strip() for x in text.splitlines() if x.strip()]
        body = "\n".join(parts[1:]) if parts else text
        numeric = [x for x in parts if re.search(r"\d", x) and len(x) > 12][:6]
        lines_out += [
            f"## {i}. {m['doc_id'][:12]} | {m['site_name']} | {m['text_chars']} 字",
            f"- 标题：{m['title']}",
            f"- URL：{m['source_url']}",
            "- 正文开头：",
            "```",
            body[:800],
            "```",
            "- 含数字/日期关键行：",
        ]
        lines_out += [f"  - {x[:180]}" for x in numeric] or ["  - （无）"]
        lines_out.append("")
    OUT.write_text("\n".join(lines_out), encoding="utf-8")
    print(f"摘要 {len(metas)} 篇 → {OUT}")


if __name__ == "__main__":
    main()

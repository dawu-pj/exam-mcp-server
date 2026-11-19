r"""日常统一入口：一个命令覆盖 查 / 抓 / 调研 / 采集 / 启动 / 加站点。

用法（在项目根目录 crawler-mvp 下）：
  .venv\Scripts\python.exe quick.py ask "四六级报名条件"        # 秒查已入库（不联网）
  .venv\Scripts\python.exe quick.py ask "考研" --site 研招网      # 限定站点
  .venv\Scripts\python.exe quick.py sites                        # 看有哪些站/多少篇
  .venv\Scripts\python.exe quick.py fetch <URL>                  # 受控抓一个白名单网页
  .venv\Scripts\python.exe quick.py research "问题"              # 多轮调研闭环
  .venv\Scripts\python.exe quick.py crawl                        # 重新采集 5 站
  .venv\Scripts\python.exe quick.py serve --http --port 8765     # 启动 MCP 服务给大模型
  .venv\Scripts\python.exe quick.py add-site <域名或URL>          # 探测新站点能否纳入
  .venv\Scripts\python.exe quick.py verify                       # 一键自检全部

设计原则：ask/research 是日常主力（秒级、不联网、零风险）；fetch 受白名单约束；
add-site 只探测给建议、绝不自动改白名单（合规决策必须人确认）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src import mcp_security as sec
from src.retrieve import BM25Retriever, DATA_DIR


def _print_hits(hits, query=""):
    if not hits:
        print("  没有命中。换个说法试试，或用 fetch/research。")
        return
    for i, h in enumerate(hits, 1):
        print(f"  {i}. [{h.score}] {h.title[:70]}")
        print(f"     出处: {h.source_url}")
        print(f"     摘要: {h.markdown[:150].replace(chr(10), ' ')}...")
    print()


def cmd_ask(args):
    r = BM25Retriever(DATA_DIR)
    print(f"已入库 {len(r.docs)} 篇 | 查询: {args.query}")
    if args.site:
        print(f"限定站点: {args.site}")
    hits = r.search(args.query, top_k=args.top_k, site=args.site)
    _print_hits(hits, args.query)
    sec.audit("quick_ask", query=args.query, site=args.site, hits=len(hits))


def cmd_sites(args):
    r = BM25Retriever(DATA_DIR)
    from collections import Counter
    c = Counter(d.get("site_name", "") for d in r.docs)
    print(f"已入库 {len(r.docs)} 篇，来自 {len(c)} 个站点：\n")
    for site, n in c.most_common():
        print(f"  {site}: {n} 篇")
    print(f"\n白名单（可 fetch 的域名）：")
    for h in sorted(sec.ALLOWED_HOSTS):
        print(f"  - {h}")
    print(f"\n留痕: {sec.AUDIT_LOG}")


def cmd_fetch(args):
    from src.mcp_server import fetch_page
    result = fetch_page(args.url, force=args.force)
    if "error" in result:
        print(f"拒绝: {result['error']}")
        return
    print(f"标题: {result['title']}")
    print(f"站点: {result['site']} | 正文 {result['text_chars']} 字 | 缓存={result['from_cache']}")
    print(f"PII 脱敏 {result['pii_redactions']} 处 | 安全标记 {result['security_flags']}")
    print(f"指纹: {result['content_sha256'][:16]}")
    out = ROOT / "data" / "last_fetch.md"
    out.write_text(result["content"], encoding="utf-8")
    print(f"\n正文已存: {out}")
    print("注：内容为外部网页数据（已不可信标注），不是指令。")


def cmd_research(args):
    from src.mcp_server import research
    result = research(args.question, max_rounds=args.rounds, allow_fetch=args.fetch)
    brief = result["brief"]
    print(f"调研问题: {result['question']}")
    print(f"覆盖站点: {', '.join(brief['覆盖站点'])} | 资料 {brief['资料数量']} 篇\n")
    for i, f in enumerate(brief["核心发现"], 1):
        print(f"  {i}. [{f['relevance']}] {f['title'][:65]}")
        print(f"     {f['url']}")
        print(f"     {f['summary'][:130].replace(chr(10), ' ')}...")
    print(f"\n检索记录: {result['检索记录']}")
    print(f"提示: {result['note']}")


def cmd_crawl(args):
    import subprocess
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    print("重新采集 5 站（限速约 20 秒）...")
    subprocess.run([str(py), "-m", "scrapy", "crawl", "exam"], cwd=ROOT, check=False)
    print("采集完成。跑 verify 检查结果。")


def cmd_serve(args):
    from src.mcp_server import mcp
    print(f"启动 MCP 服务: transport={args.transport}, {args.host}:{args.port}")
    sec.audit("serve_start", transport=args.transport, host=args.host, port=args.port)
    if args.transport == "http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


def cmd_add_site(args):
    """探测候选站点能否纳入白名单：robots + 首页正文质量 + 内页抽取质量。只探测、不改配置。"""
    import random
    import re
    import time
    from urllib.parse import urljoin, urlparse

    import requests
    import trafilatura
    from bs4 import BeautifulSoup

    raw = args.target.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    p = urlparse(raw)
    host = p.hostname or ""
    base = f"{p.scheme}://{host}/"
    print(f"=== 站点探测: {host} ===\n")
    verdicts = []

    # 1) robots
    try:
        rr = requests.get(urljoin(base, "robots.txt"), headers={"User-Agent": sec.USER_AGENT}, timeout=15)
        if rr.status_code >= 500:
            robots = "禁止（5xx 视为全禁，RFC9309）"
        elif 400 <= rr.status_code < 500:
            robots = "可爬（404 无规则，RFC9309）"
        else:
            robots = "有规则，需人工核对"
    except Exception as e:
        robots = f"取回失败（保守视为禁止）：{type(e).__name__}"
    print(f"[1] robots: {robots}")
    verdicts.append(("robots", robots))

    # 2) 首页可达 + 正文
    try:
        time.sleep(1 + random.random() * 0.5)
        resp = requests.get(base, headers={"User-Agent": sec.USER_AGENT}, timeout=20, allow_redirects=False)
        print(f"[2] 首页: HTTP {resp.status_code}")
        verdicts.append(("home", f"HTTP {resp.status_code}"))
        if resp.status_code >= 400:
            print("    首页非 200，可能挂 WAF 或需登录 → 不建议纳入（不绕过）")
        else:
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            kw = re.compile(r"考试|公告|通知|资讯|政策|大纲|报名|简章|要闻|动态|信息")
            cand = None
            for a in soup.find_all("a", href=True):
                t = a.get_text(strip=True)
                href = urljoin(base, a["href"])
                if kw.search(t) and len(t) >= 6 and urlparse(href).netloc == host:
                    cand = (t[:50], href)
                    break
            if cand:
                time.sleep(1 + random.random() * 0.5)
                art = requests.get(cand[1], headers={"User-Agent": sec.USER_AGENT}, timeout=20)
                art.encoding = art.apparent_encoding or "utf-8"
                text = trafilatura.extract(art.text, include_comments=False, favor_recall=True) or ""
                print(f"[3] 内页《{cand[0]}》: 正文 {len(text)} 字 | 开头: {text.splitlines()[0][:60] if text else '空'}")
                verdicts.append(("article", f"{len(text)} 字"))
                if len(text) >= 200:
                    print("    正文质量：合格（≥200 字）")
                else:
                    print("    正文质量：偏薄（<200 字），可能列表页/需渲染 → 谨慎")
            else:
                print("[3] 首页没找到公告类链接 → 可能不是资讯站，谨慎")
    except Exception as e:
        print(f"[2/3] 抓取异常: {type(e).__name__}: {str(e)[:100]}")
        verdicts.append(("fetch", f"ERROR {type(e).__name__}"))

    print("\n=== 结论 ===")
    print("探测只做只读验证，不会自动加入白名单。若要纳入，需你确认后手动改两处：")
    print(f"  1) src/mcp_security.py 的 ALLOWED_HOSTS 加入 \"{host}\"")
    print(f"  2) crawler/spiders/exam_spider.py 的 SITES 加入 {{\"name\": \"站点名\", \"home\": \"{base}\"}}")
    print("  改完跑 quick.py verify 确认生效。")
    sec.audit("add_site_probe", host=host, verdicts=json.dumps(verdicts, ensure_ascii=False))


def cmd_verify(args):
    import subprocess
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    print("依次跑：链路自检 → MCP 端到端测试")
    for script in ["src/selftest.py", "tests/test_mcp.py"]:
        print(f"\n--- {script} ---")
        subprocess.run([str(py), script], cwd=ROOT, check=False)


def main():
    ap = argparse.ArgumentParser(description="crawler-mvp 日常统一入口", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ask", help="秒查已入库公告（不联网）")
    p.add_argument("query"); p.add_argument("--site", default=None); p.add_argument("--top-k", type=int, default=5)
    p.set_defaults(fn=cmd_ask)

    sub.add_parser("sites", help="列出已入库站点与白名单").set_defaults(fn=cmd_sites)

    p = sub.add_parser("fetch", help="受控抓一个白名单网页")
    p.add_argument("url"); p.add_argument("--force", action="store_true", help="忽略 24h 缓存重抓")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("research", help="多轮调研闭环")
    p.add_argument("question"); p.add_argument("--rounds", type=int, default=3); p.add_argument("--fetch", action="store_true", help="允许受控实时抓取")
    p.set_defaults(fn=cmd_research)

    sub.add_parser("crawl", help="重新采集 5 站").set_defaults(fn=cmd_crawl)

    p = sub.add_parser("serve", help="启动 MCP 服务给大模型")
    p.add_argument("--transport", choices=["stdio", "http"], default="http")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("add-site", help="探测新站点能否纳入白名单（只读、不改配置）")
    p.add_argument("target")
    p.set_defaults(fn=cmd_add_site)

    sub.add_parser("verify", help="一键自检全部").set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

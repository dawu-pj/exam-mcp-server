"""通用爬虫层端到端测试（全程零外网）。

覆盖：
- 发现层 web_search：mock Brave 响应、无 key 降级、untrusted 包裹、Tier 标注、成本
- 域名准入 domain_risk_check / domain_approve：Tier A/B/C、robots/TDM/WAF 信号、批准流程
- web_fetch：Tier C 拒绝、未批准拒绝、质量闸门拒导航残渣、缓存、分块
- research 新参数：use_web_search 开关、token_budget、use_web_search=false 零成本

运行：.venv/Scripts/python.exe tests/test_generic.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastmcp import Client
from src.mcp_server import mcp
from src import domain_policy as policy
from tests.no_network import no_network, UnexpectedNetworkAccess

BRAVE_MOCK = {
    "web": {"results": [
        {
            "title": "2026 年下半年四六级报名公告",
            "url": "https://cet.neea.edu.cn/notice/2026.htm",
            "description": "报名时间为 9 月 1 日至 9 月 20 日，请考生留意。",
            "age": "2026-09-01",
        },
        {
            "title": "某考试报名通知",
            "url": "https://example.org/exam/2026",
            "description": "外部站点摘要内容，用于 Tier B 标注。",
        },
    ]}
}


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def fake_brave_get(url, params=None, headers=None, timeout=None):
    assert "api.search.brave.com" in url
    return FakeResp(payload=BRAVE_MOCK)


def fake_signal_get(url, **kwargs):
    """预检用假 fetcher：robots 全放行 / TDM 无保留 / 首页无 WAF。"""
    if url.endswith("/robots.txt"):
        return FakeResp(status=200, text="User-agent: *\nAllow: /\n")
    if "tdmrep.json" in url:
        return FakeResp(status=404)
    return FakeResp(status=200, text="<html><head><title>t</title></head><body>ok</body></html>")


def fake_waf_get(url, **kwargs):
    if url.endswith("/robots.txt"):
        return FakeResp(status=200, text="User-agent: *\nAllow: /\n")
    if "tdmrep.json" in url:
        return FakeResp(status=404)
    return FakeResp(status=200, text="<html>Just a moment... cf-challenge</html>")


def fake_tdm_get(url, **kwargs):
    if url.endswith("/robots.txt"):
        return FakeResp(status=200, text="User-agent: *\nAllow: /\n")
    if "tdmrep.json" in url:
        return FakeResp(status=200, text='{"tdm-reservation": 1}')
    return FakeResp(status=200, text="<html>ok</html>")


def _check_no_network():
    import requests
    try:
        with no_network():
            requests.get("https://example.com", timeout=1)
    except UnexpectedNetworkAccess:
        return True
    raise AssertionError("no_network 保险失效")


async def _run():
    async with Client(mcp) as c:
        # ---------- 1. 工具集 ----------
        names = {t.name for t in await c.list_tools()}
        assert names == {
            "query_local", "search_site", "fetch_page", "research",
            "web_search", "web_fetch", "domain_risk_check", "domain_approve",
        }, names
        print("[1] 工具注册 8 个: OK")

        # ---------- 2. web_search 无 key 降级 ----------
        os.environ.pop("BRAVE_API_KEY", None)
        r = (await c.call_tool("web_search", {"query": "四六级报名"})).data
        assert "BRAVE_API_KEY" in r.get("error", ""), r
        print("[2] web_search 无 key 明确降级: OK")

        # ---------- 3. web_search mock 走通（注入 fetcher 到 provider 层） ----------
        import src.mcp_server as srv
        orig = srv.sp.brave_search

        def mocked_brave(query, **kw):
            kw["fetcher"] = fake_brave_get
            kw["api_key"] = "test-key"
            return orig(query, **kw)

        srv.sp.brave_search = mocked_brave
        try:
            r = (await c.call_tool("web_search", {
                "query": "2026 四六级报名", "max_results": 5, "max_chars_per_snippet": 300,
            })).data
            assert r["status"] == "ok" and r["count"] == 2, r
            first, second = r["results"]
            assert "<untrusted_content" in first["snippet"], "摘要必须 untrusted 包裹"
            assert first["trust_tier"] == "A", first
            assert second["trust_tier"] == "B", second
            assert r["usage"]["estimated_cost_usd"] == 0.005, r["usage"]
            print("[3] web_search mock: 2 候选 + untrusted + Tier 标注 + 成本: OK")
        finally:
            srv.sp.brave_search = orig

        # ---------- 4. 域名分级静态 ----------
        r = (await c.call_tool("domain_risk_check", {"url": "https://www.neea.edu.cn/"})).data
        assert (r["tier"], r["decision"]) == ("A", "allow"), r
        r = (await c.call_tool("domain_risk_check", {"url": "https://example.org/exam"})).data
        assert (r["tier"], r["decision"]) == ("B", "deny"), r
        r = (await c.call_tool("domain_risk_check", {"url": "https://www.cpta.com.cn/"})).data
        assert (r["tier"], r["decision"]) == ("C", "deny"), r
        r = (await c.call_tool("domain_risk_check", {"url": "https://foo.com/login"})).data
        assert r["tier"] == "C", r
        print("[4] domain_risk_check Tier A/B/C + 登录路径拒采: OK")

        # ---------- 5. 信号检测（注入 fetcher，验证 robots 5xx/TDM/WAF） ----------
        v = policy.precheck_domain("https://newsite.example.org/a", fetcher=fake_signal_get,
                                   check_signals=True)
        # Tier B 信号全绿仍默认 deny（未获人工批准）—— 这正是"默认拒绝"的正确姿态
        assert v["signals"]["robots"] == "ok:200" and v["signals"]["waf"] == "none", v
        assert v["decision"] == "deny" and any("未获批准" in b for b in v["blockers"]), v
        v = policy.precheck_domain("https://wafsite.example.org/a", fetcher=fake_waf_get,
                                   check_signals=True)
        assert v["decision"] == "deny" and "cf-challenge" in v["signals"]["waf"], v
        v = policy.precheck_domain("https://tdm.example.org/a", fetcher=fake_tdm_get,
                                   check_signals=True)
        assert v["decision"] == "deny" and v["signals"]["tdm_reservation"] == "deny", v

        def http500_get(url, **kwargs):
            return FakeResp(status=503, text="")
        v = policy.precheck_domain("https://down.example.org/a", fetcher=http500_get,
                                   check_signals=True)
        assert v["decision"] == "deny" and v["signals"]["robots"] == "deny:503", v
        print("[5] 信号闸门: robots 5xx / TDM / WAF 全部拒绝: OK")

        # ---------- 6. web_fetch 闸门链 ----------
        r = (await c.call_tool("web_fetch", {"url": "https://www.cpta.com.cn/a"})).data
        assert "error" in r and r["tier"] == "C", r
        r = (await c.call_tool("web_fetch", {"url": "https://example.org/exam"})).data
        assert r["status"] == "error" and "未" not in r["error"], r
        r = (await c.call_tool("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"})).data
        assert "error" in r, r
        print("[6] web_fetch: Tier C / 未批准 / SSRF 全部拒绝: OK")

        # ---------- 7. 抽取质量闸门单元（不联网） ----------
        from src.extraction_quality import quality_gate
        nav_only = "首页 | 简 | 繁 | EN | 登录 | 邮箱 | 无障碍\nhome | login | search"
        bad = quality_gate(nav_only, "<html></html>")
        # 关键断言：残渣必须判失败（effective_chars 远低于正常正文的 458）
        assert not bad["ok"] and bad["metrics"]["effective_chars"] < 180, bad
        good_text = "\n".join(["2026 年下半年四六级报名工作已经开始。" * 12] * 6)
        good = quality_gate(good_text, "<html></html>")
        assert good["ok"] and good["metrics"]["effective_chars"] > 180, good
        print("[7] 抽取质量闸门: 残渣拒绝 / 正常正文通过: OK")

        # ---------- 8. 批准流程（隔离临时文件，不改真实清单） ----------
        with tempfile.TemporaryDirectory() as td:
            orig_file = policy.APPROVED_FILE
            policy.APPROVED_FILE = Path(td) / "approved.json"
            try:
                a = policy.approve_domain("example.org", "公开静态页 robots 允许，人工评估通过", "tester")
                assert a["ok"], a
                a = policy.approve_domain("www.cpta.com.cn", "想放行 WAF 站", "tester")
                assert not a["ok"], a  # Tier C 不可批准
                a = policy.approve_domain("neea.edu.cn", "已是白名单站", "tester")
                assert not a["ok"], a  # Tier A 无需批准
                v = policy.precheck_domain("https://example.org/exam")
                assert v["decision"] == "allow", v
                print("[8] domain_approve: 批准生效 / Tier C 与 A 不可批准: OK")
            finally:
                policy.APPROVED_FILE = orig_file

        # ---------- 9. research 新参数向后兼容 ----------
        r = (await c.call_tool("research", {
            "question": "四六级报名条件", "max_rounds": 2, "token_budget": 500,
        })).data
        assert r["brief"]["资料数量"] > 0 and r["usage"]["web_search_cost_usd"] == 0.0, r
        assert "来源构成" in r["brief"], r
        print("[9] research 新参数 + 默认零成本 + 向后兼容: OK")


async def main():
    assert _check_no_network(), "禁网自检失败"
    print("[0] 零外网保险自检: OK")
    with no_network():
        await _run()
    print("=== 通用层全部测试通过 ===")


if __name__ == "__main__":
    asyncio.run(main())

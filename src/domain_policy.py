"""通用抓取的域名准入策略：Tier A/B/C 分级 + 新域预检 + 显式批准。

设计原则（对齐《通用爬虫演进调研与实施方案》P3）：
- allow_url() 仍是唯一出网入口，本模块不提供任何 bypass 参数。
- Tier A（已验证白名单 7 站）：原样放行。
- Tier B（公开静态页、未见拒绝信号）：**默认拒绝**，须经人工显式批准后
  写入 approved_domains.json，allow_url() 才会放行；批准全程留痕。
- Tier C（登录态/付费墙/PI 密集/政府敏感/儿童/成人/已知 WAF）：永久拒绝，
  不提供批准入口（要放行必须改代码并写明法律依据）。

预检 precheck_domain() 只做判定、不抓正文；robots/TDM/meta 信号检测为
可选联网分支，接受可注入 fetcher 便于离线测试（no_network 下不误触发）。

合规依据：合规调研 §5 闸门表 G-01~G-20、§6 拒采清单；非法律意见。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from src import mcp_security as sec

# ---------- Tier C：硬拒域名/模式（永久拒绝，无批准入口） ----------
# 依据：合规调研 §6 必须拒采清单 + 已实测 WAF 站（source_recheck.json）
TIER_C_HOSTS: frozenset[str] = frozenset({
    "www.cpta.com.cn",      # 中国人事考试网：实测 405 + WAF
})

TIER_C_HOST_PATTERNS = (
    "login.", "passport.", "account.", "pay.", "checkout.", "admin.",
)

# 内容/路径层拒采信号：命中任一即 Tier C
TIER_C_PATH_PATTERNS = (
    "/login", "/signin", "/oauth", "/pay", "/checkout", "/captcha", "/verify",
)

# 个人信息密集 / 敏感站点域名关键词（命中即需人工审批，默认 Tier C）
PI_DENSE_HINTS = (
    "resume", "job.", "hr.", "dating", "social", "forum", "bbs.",
    "medical", "health", "bank", "gov.cn", "kid", "child",
)

APPROVED_FILE = Path(__file__).resolve().parent.parent / "data" / "approved_domains.json"


def _now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def approved_hosts() -> frozenset[str]:
    """读取人工批准的 Tier B 域名集合。文件不存在/损坏时返回空集（默认拒绝）。"""
    if not APPROVED_FILE.exists():
        return frozenset()
    try:
        data = json.loads(APPROVED_FILE.read_text(encoding="utf-8"))
        hosts = data.get("hosts", [])
        return frozenset(h.lower() for h in hosts if isinstance(h, str))
    except Exception:
        sec.audit("approved_file_error", path=str(APPROVED_FILE))
        return frozenset()


def is_approved(host: str) -> bool:
    return host.lower() in approved_hosts()


def classify_domain(url: str) -> tuple[str, str]:
    """返回 (tier, 原因)。纯静态判定，不联网。

    Tier A: 已验证 7 站白名单
    Tier B: 未在拒采清单、域名形态像公开静态页（默认仍拒绝，需显式批准）
    Tier C: 拒采清单命中 / 登录付费等路径 / PI 密集域名
    """
    try:
        p = urlsplit(url)
    except Exception:
        return "C", "URL 解析失败"
    host = (p.hostname or "").lower()
    path = (p.path or "").lower()
    if not host:
        return "C", "无主机名"
    if host in sec.ALLOWED_HOSTS:
        return "A", "已验证白名单站"
    if host in TIER_C_HOSTS:
        return "C", f"已知 WAF/拒采站：{host}"
    if any(pat in host for pat in TIER_C_HOST_PATTERNS):
        return "C", f"登录/账号/支付类域名形态：{host}"
    if any(pat in path for pat in TIER_C_PATH_PATTERNS):
        return "C", "路径命中登录/支付/验证码等拒采模式"
    if any(hint in host for hint in PI_DENSE_HINTS):
        return "C", "个人信息密集/敏感类域名（拒采清单）"
    return "B", "公开站，默认拒绝抓取；需 domain_risk_check 预检 + 人工批准"


def precheck_domain(
    url: str,
    fetcher=None,
    *,
    check_signals: bool = False,
) -> dict:
    """新域预检：只判定不抓正文。

    Args:
        url: 待判定 URL
        fetcher: 可注入的 HTTP GET 函数（签名同 requests.get），None 则用 sec.requests.get
        check_signals: 是否联网检测 robots/TDM/meta 信号（默认 False，纯静态判定）

    Returns:
        含 tier / decision / reasons / signals / 建议的字典。
    """
    tier, reason = classify_domain(url)
    host = (urlsplit(url).hostname or "").lower()
    signals: dict = {
        "robots": "unchecked",
        "tdm_reservation": "unchecked",
        "meta_noindex": "unchecked",
        "waf": "unchecked",
    }
    blockers: list[str] = []

    if tier == "A":
        decision = "allow"
    elif tier == "C":
        decision = "deny"
        blockers.append(reason)
    else:
        # Tier B：默认拒绝，除非已显式批准
        if is_approved(host):
            decision = "allow"
        else:
            decision = "deny"
            blockers.append("Tier B 未获批准（需人工 domain_approve）")

    if check_signals and tier != "C":
        get = fetcher or sec.requests.get
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        # G-01/G-03: robots 5xx/401/403/429 从严拒绝
        try:
            rr = get(origin + "/robots.txt", headers={"User-Agent": sec.USER_AGENT}, timeout=10)
            status = rr.status_code
            if status >= 500 or status in (401, 403, 429):
                signals["robots"] = f"deny:{status}"
                blockers.append(f"robots HTTP {status}（从严拒绝）")
                decision = "deny"
            elif status == 404:
                signals["robots"] = "none:404"
            else:
                txt = rr.text.lower()
                if "tdm-reservation: 1" in txt or "disallow: /" in txt[:2000]:
                    signals["robots"] = "deny:signal"
                    blockers.append("robots 含全站 Disallow 或 TDM 保留信号")
                    decision = "deny"
                else:
                    signals["robots"] = "ok:200"
        except Exception as e:
            signals["robots"] = f"error:{type(e).__name__}"
            blockers.append(f"robots 检测异常：{type(e).__name__}（保守拒绝）")
            decision = "deny"

        # G-06: TDM 权利保留
        try:
            tr = get(origin + "/.well-known/tdmrep.json",
                     headers={"User-Agent": sec.USER_AGENT}, timeout=10)
            # 统一去除空格后比较（JSON 可能带/不带空格），匹配模式也必须无空格
            compact = tr.text.replace(" ", "").replace("\n", "")
            if tr.status_code == 200 and '"tdm-reservation":1' in compact:
                signals["tdm_reservation"] = "deny"
                blockers.append("TDM 权利保留（tdm-reservation=1）")
                decision = "deny"
            else:
                signals["tdm_reservation"] = "none"
        except Exception:
            signals["tdm_reservation"] = "unchecked"

        # G-09/G-11: meta noindex / WAF 特征（仅看首页头部，不取正文入库）
        try:
            hr = get(url, headers={"User-Agent": sec.USER_AGENT}, timeout=10)
            head = hr.text[:8000].lower()
            waf_words = ("captcha", "cf-challenge", "datadome", "akamai bot", "just a moment")
            hit_waf = next((w for w in waf_words if w in head), None)
            if hit_waf:
                signals["waf"] = f"deny:{hit_waf}"
                blockers.append(f"WAF/验证码特征：{hit_waf}")
                decision = "deny"
            else:
                signals["waf"] = "none"
            if 'name="robots"' in head and ("noindex" in head or "none" in head[:head.find('name="robots"') + 120]):
                signals["meta_noindex"] = "deny"
                blockers.append("meta robots noindex/none")
                decision = "deny"
            elif hr.status_code in (402, 403, 429):
                signals["waf"] = f"deny:http{hr.status_code}"
                blockers.append(f"HTTP {hr.status_code}（疑似 WAF/限速）")
                decision = "deny"
            else:
                signals["meta_noindex"] = "none"
        except Exception as e:
            signals["waf"] = f"error:{type(e).__name__}"

    result = {
        "url": url,
        "host": host,
        "tier": tier,
        "decision": decision,
        "reason": reason,
        "signals": signals,
        "blockers": blockers,
        "hint": (
            "可用 domain_risk_check(url, check_signals=true) 做联网预检；"
            "Tier B 经人工评估后可 domain_approve 加入批准清单。" if tier == "B"
            else "Tier C 为永久拒采，无批准入口。" if tier == "C"
            else "Tier A 已验证，可直接抓取。"
        ),
    }
    sec.audit("domain_risk_check", host=host, tier=tier, decision=decision,
              signals=signals, blockers=blockers)
    return result


def approve_domain(host: str, reason: str, operator: str = "human") -> dict:
    """人工显式批准一个 Tier B 域名写入批准清单（不提供 bypass 参数）。

    仅允许 Tier B；Tier A 无需批准，Tier C 不可批准。审批全程留痕。
    """
    host = host.strip().lower()
    tier, why = classify_domain(f"https://{host}/")
    if tier == "C":
        return {"ok": False, "error": f"Tier C 不可批准：{why}"}
    if tier == "A":
        return {"ok": False, "error": "Tier A 已在白名单，无需批准"}
    if not reason or len(reason.strip()) < 8:
        return {"ok": False, "error": "批准理由至少 8 字（用于审计留痕）"}

    data = {"hosts": [], "history": []}
    if APPROVED_FILE.exists():
        try:
            data = json.loads(APPROVED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    hosts = set(data.get("hosts", []))
    hosts.add(host)
    data["hosts"] = sorted(hosts)
    data.setdefault("history", []).append({
        "host": host, "reason": reason.strip(), "operator": operator, "at": _now_cn(),
    })
    APPROVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    sec.audit("domain_approve", host=host, reason=reason.strip(), operator=operator)
    return {"ok": True, "host": host, "tier": "B", "note": "已加入批准清单，allow_url() 现在放行"}

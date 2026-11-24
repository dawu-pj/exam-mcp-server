"""抽取质量闸门：拦住"HTTP 200 却只抽到导航残渣"的静默失败。

一手依据（data/generic_probe_evidence.json）：
- 已适配站 neea：trafilatura 抽出 1028 字符正文，有效内容行多 → 应通过
- 陌生站 gov.cn 政策页：trafilatura 只抽 62 字符、bs4 fallback 122 字符，
  全是"首页|简|繁|EN|登录"导航残渣 → 必须判失败，绝不喂给大模型

判据（不依赖具体站点，通用）：
1. 有效内容字符数：只统计长度 >= MIN_LINE_CHARS 的行并求和。
   导航残渣都是短行，即使拼接很长也过不了这一关。
2. 导航词密度：导航词命中数 / 总行数过高 → 判残渣。
3. 双抽取取优：trafilatura 主，bs4 启发式兜底，取有效内容更多的那个；
   两者都不达标 = 抽取失败。

这不是"换个抽取器就能解决"——gov.cn 那页正文在 JS 里，静态 HTML 根本没有，
所以正确行为是判失败并提示（空 shell 需 JS 渲染，本方案暂不启用浏览器）。
"""
from __future__ import annotations

import re

MIN_LINE_CHARS = 20          # 少于此长度的行视为导航/碎片，不计入有效内容
MIN_EFFECTIVE_CHARS = 180    # 有效内容字符数门槛（neea 1028 远超，gov 残渣 <50）
MAX_NAV_DENSITY = 0.35       # 导航词命中行占比上限

NAV_WORDS = re.compile(
    r"^(首页|登录|注册|个人中心|退出|邮箱|无障碍|简|繁|EN|导航|网站地图|"
    r"联系我们|版权所有|联系我们|搜索|主站|返回顶部|打印|关闭)$",
    re.I,
)


def _effective_chars(text: str) -> int:
    """只统计长度达标的行——导航残渣由大量短行构成，这里会被过滤掉。"""
    if not text:
        return 0
    total = 0
    for line in re.split(r"[\n\r]+", text):
        line = line.strip(" \t|·-—　")
        if len(line) >= MIN_LINE_CHARS:
            total += len(line)
    return total


def _nav_density(text: str) -> float:
    lines = [ln.strip(" \t|·-—　") for ln in re.split(r"[\n\r]+", text or "") if ln.strip()]
    if not lines:
        return 1.0
    # 逐行全匹配；残渣行常是"首页 | 简 | 繁 | EN"这种多导航词拼接，拆词后再判
    hits = 0
    for ln in lines:
        tokens = [t.strip(" \t|·-—　") for t in re.split(r"[|｜\s]+", ln) if t.strip()]
        if tokens and all(NAV_WORDS.fullmatch(t) for t in tokens):
            hits += 1
    return round(hits / len(lines), 3)


def bs4_fallback(html: str, max_paras: int = 40) -> str:
    """备用抽取：去脚本样式后取较长的段落/块文本（trafilatura 失效时的兜底）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "article", "div"])]
        paras = [p for p in paras if len(p) >= MIN_LINE_CHARS]
        paras.sort(key=len, reverse=True)
        return "\n".join(paras[:max_paras])
    except Exception:
        return ""


def quality_gate(
    text: str,
    html: str | None = None,
    *,
    min_effective: int = MIN_EFFECTIVE_CHARS,
    try_fallback: bool = True,
) -> dict:
    """判定抽取质量，返回 {ok, text, reason, metrics, extractor}。

    ok=False 时 text 为空（调用方应把它当成"抓取失败"，不是"正文很短"）。
    """
    primary = text or ""
    primary_eff = _effective_chars(primary)

    best_text, best_eff, extractor = primary, primary_eff, "trafilatura"
    if try_fallback and html and best_eff < min_effective:
        fb = bs4_fallback(html)
        fb_eff = _effective_chars(fb)
        if fb_eff > best_eff:
            best_text, best_eff, extractor = fb, fb_eff, "bs4_fallback"

    nav = _nav_density(best_text)
    metrics = {
        "effective_chars": best_eff,
        "raw_chars": len(best_text),
        "nav_density": nav,
        "min_effective": min_effective,
        "extractor": extractor,
    }
    reason = "OK"
    ok = True
    if best_eff < min_effective:
        ok = False
        reason = (
            f"抽取质量不足：有效内容 {best_eff} 字符 < 门槛 {min_effective}"
            "（疑似空壳页/JS 渲染/导航残渣，本方案不启用浏览器渲染）"
        )
    elif nav > MAX_NAV_DENSITY:
        ok = False
        reason = f"导航残渣密度过高：{nav} > {MAX_NAV_DENSITY}"
    return {
        "ok": ok,
        "text": best_text if ok else "",
        "reason": reason,
        "metrics": metrics,
    }

"""清洗规则：中文 PII 脱敏（B1）+ URL 规范化 + 内容指纹（B2）。
设计原则：结构化标识符用校验算法精确识别；中文姓名误报高，只标记不删除。
将来 Alcatraz 成熟可只替换本文件，不动 pipeline。
"""
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# ---------- 身份证 18 位（GB 11643 校验位算法）----------
ID_RE = re.compile(r"(?<!\d)([1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3})([0-9Xx])(?!\d)")
WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK_CODES = "10X98765432"


def _valid_id(body: str, check: str) -> bool:
    total = sum(int(c) * w for c, w in zip(body, WEIGHTS))
    return CHECK_CODES[total % 11] == check.upper()


# ---------- 手机号（中国大陆 11 位号段）----------
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def mask_pii(text: str):
    """返回 (脱敏文本, 命中记录列表)。中文姓名不自动删除，避免误伤正文。"""
    hits = []

    def id_sub(m):
        if _valid_id(m.group(1), m.group(2)):
            hits.append({"type": "ID_CARD", "value": m.group(0)})
            return m.group(1)[:6] + "********" + m.group(2)
        return m.group(0)

    out = ID_RE.sub(id_sub, text)

    def phone_sub(m):
        hits.append({"type": "PHONE", "value": m.group(0)})
        return m.group(1)[:3] + "****" + m.group(1)[-4:]

    out = PHONE_RE.sub(phone_sub, out)

    def email_sub(m):
        hits.append({"type": "EMAIL", "value": m.group(0)})
        local = m.group(0).split("@")[0]
        return (local[:2] + "***" if len(local) > 2 else "***") + "@" + m.group(0).split("@")[1]

    out = EMAIL_RE.sub(email_sub, out)
    return out, hits


# ---------- URL 规范化（B2）----------
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/+", "/", parts.path)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def content_fingerprint(text: str) -> str:
    """正文归一化后 SHA256，用于近重复检测与重访比对（B2）。"""
    normalized = re.sub(r"\s+", "", text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

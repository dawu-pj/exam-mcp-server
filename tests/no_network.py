"""测试零外网保险：任何意外的真实 HTTP 请求都会立刻失败。

借鉴 13ft 的 `patch.object(Session, 'request', side_effect=AssertionError(...))`
思路，但用 context manager 封装，便于按需包裹某段测试，而非全局污染整个进程。

为什么需要：端到端测试若不小心走到真实站点，会
  1) 给目标站制造非预期抓取压力（违反咱们"严格限速留痕"的合规基线）；
  2) 让测试结果依赖外网/站点状态，变得不可复现。

注意：本项目的纯本地工具（query_local/search_site/research 默认 allow_fetch=False）
与安全闸门的 SSRF 拒绝路径都不发真实请求，因此可在这些测试上安全启用。
真正需要联网的测试（如 src/verify_remote.py 线上验证）不要用本保险。
"""
from contextlib import contextmanager
from unittest.mock import patch

import requests


class UnexpectedNetworkAccess(AssertionError):
    """测试期间发生了未被允许的真实网络请求。"""


@contextmanager
def no_network():
    """上下文内禁止一切 requests 出网；命中即抛 UnexpectedNetworkAccess。"""
    def _block(*args, **kwargs):
        raise UnexpectedNetworkAccess(
            "测试期间禁止真实网络请求：可能被 patch 漏掉的联网路径"
        )

    with patch.object(requests.sessions.Session, "request", side_effect=_block):
        yield

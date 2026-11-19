r"""链路自检：不依赖目标站点，用内嵌样本验证 抽取 -> 分词 -> BM25 检索 全链路可用。
运行：.venv\Scripts\python.exe src\selftest.py
"""
import re
import sys

import jieba
import trafilatura
from rank_bm25 import BM25Okapi

SAMPLE_HTML = """
<html><head><title>示例文章</title></head><body>
<nav>首页 导航 广告 登录 注册 推荐阅读</nav>
<div class="sidebar">相关链接 热门标签 版权所有</div>
<article>
  <h1>大模型需要高质量的正文数据</h1>
  <p>网络爬虫为大模型提供信息获取能力时，正文抽取质量决定了后续检索与问答的上限。
  导航、广告、页脚这类模板内容必须被剔除，否则会污染向量库并浪费模型的上下文预算。</p>
  <p>trafilatura 是 FineWeb 数据集采用的正文抽取工具，能够从杂乱的 HTML 中稳定提取正文，
  并保留标题层级。它在本项目中作为通用抽取的兜底方案。</p>
</article>
<footer>备案号 联系方式 友情链接</footer>
</body></html>
"""


def check_extract():
    text = trafilatura.extract(SAMPLE_HTML, include_comments=False, include_tables=True)
    assert text, "trafilatura 抽取结果为空"
    assert "大模型需要高质量的正文数据" in text, "正文标题丢失"
    assert "FineWeb" in text, "正文段落丢失"
    assert "备案号" not in text and "友情链接" not in text, "页脚模板未被剔除"
    assert "相关链接" not in text, "侧边栏未被剔除"
    print("[1/4] 正文抽取 OK   正文字符数:", len(text))
    print("      正文开头:", text.splitlines()[0][:40])
    return text


def check_tokenize(text):
    tokens = [w for w in jieba.lcut(text) if w.strip() and not re.match(r"^\W+$", w)]
    assert len(tokens) > 20, "分词结果过少，中文分词可能异常"
    print("[2/4] 中文分词 OK   token 数:", len(tokens))
    print("      前 10 个 token:", tokens[:10])
    return tokens


def check_bm25():
    docs = [
        "网络爬虫为大模型提供信息获取能力 正文抽取质量决定检索上限",
        "trafilatura 是 FineWeb 数据集采用的正文抽取工具 保留标题层级",
        "天气今天晴转多云 适合出行 注意防晒",
    ]
    corpus = [[w for w in jieba.lcut(d) if w.strip()] for d in docs]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores([w for w in jieba.lcut("大模型正文抽取工具") if w.strip()])
    top = scores.argmax()
    assert top in (0, 1), f"BM25 检索命中错误文档: {top}"
    print("[3/4] BM25 检索 OK  命中文档索引:", top, " 得分:", [round(float(s), 3) for s in scores])


def check_sidecar():
    from pydantic import BaseModel

    class MetadataSidecar(BaseModel):
        source_url: str
        fetched_at: str
        http_status: int
        parser_version: str
        index_version: str
        lang: str = "zh"

    m = MetadataSidecar(
        source_url="https://example.com/a",
        fetched_at="2026-11-19T16:00:00+08:00",
        http_status=200,
        parser_version="trafilatura-2.0.0",
        index_version="v1",
    )
    assert m.lang == "zh"
    print("[4/4] 元数据契约 OK 字段:", list(m.model_dump().keys()))


if __name__ == "__main__":
    print("=== crawler-mvp 链路自检 ===")
    print("Python:", sys.version.split()[0])
    try:
        text = check_extract()
        check_tokenize(text)
        check_bm25()
        check_sidecar()
    except AssertionError as e:
        print("自检失败:", e)
        sys.exit(1)
    print("=== 全链路通过 ===")

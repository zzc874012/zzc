"""网络搜索工具：通过百度千帆 AI 搜索 API 获取全网实时信息。

依赖：``requests``（已在 requirements.txt 中声明，无需新增）。
鉴权：从 AppConfig.search.baidu_api_key 读取，不硬编码。

百度千帆 web_search API 文档：
https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5
"""

from __future__ import annotations

import json
from typing import Any

import requests

from .base import Tool

_API_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
_DEFAULT_TIMEOUT = 15.0  # 秒
_DEFAULT_TOP_K = 5


class SearchError(RuntimeError):
    """搜索过程中的业务/网络错误，handler 捕获后转换为给 LLM 的文本。"""


class WebSearchToolset:
    """把百度千帆 web_search 封装为可供 LLM 调用的 searchWeb 工具。

    设计遵循现有 FileOpsToolset 模式：``tools()`` 返回 Tool 列表，
    handler 是同步方法，返回字符串结果；异常会被上层 ToolRegistry 统一捕获。
    """

    name = "WebSearch"

    def __init__(self, baidu_api_key: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._api_key = (baidu_api_key or "").strip()
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool 注册
    # ------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="searchWeb",
                description=(
                    "调用百度 AI 搜索获取互联网上的最新信息。"
                    "传入一段中文或英文关键词或问题（query），"
                    "返回搜索结果的标题、摘要和来源 URL，可用于回答时效性要求高、"
                    "或需要外部事实支撑的问题。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "要搜索的关键词、问题或主题。"
                            "建议简洁明确（例如 'Python 3.13 新特性'）。",
                        },
                    },
                    "required": ["query"],
                },
                handler=self.search_web,
            ),
        ]

    # ------------------------------------------------------------------
    # 实际搜索逻辑
    # ------------------------------------------------------------------
    def search_web(self, query: str) -> str:
        """handler 入口：接收 query，返回给 LLM 的格式化文本。

        与 FileOpsToolset 的风格保持一致：
        - 参数/配置/网络/API 错误 -> raise，由 ToolRegistry 转成 ok=False 的结果
        - 搜索成功有结果 -> 返回格式化文本
        - 搜索成功但无结果 -> 返回友好提示（视为工具正常执行）
        """
        query = (query or "").strip()
        if not query:
            raise ValueError("query 参数不能为空")

        # 1. 配置缺失 —— 抛异常，让 ToolRegistry 记录 ok=False
        if not self._api_key:
            raise SearchError(
                "未配置百度搜索 API Key。"
                "请在 config.ini 的 [search] 小节添加 baidu_api_key 配置项。"
            )

        # 2. 截断超长 query（千帆限制：72 字符，中文算 2）
        if len(query) > 72:
            query = query[:72]

        references = self._call_api(query)
        return self._format_references(references, query)

    # ------------------------------------------------------------------
    # HTTP 调用 & 结果解析
    # ------------------------------------------------------------------
    def _call_api(self, query: str) -> list[dict[str, Any]]:
        """发送实际 HTTP 请求并提取 references 列表。失败时抛 SearchError。"""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [
                {"type": "web", "top_k": _DEFAULT_TOP_K},
            ],
            "query_policy": {"enable_rewrite": True},
        }

        try:
            response = requests.post(
                _API_URL,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise SearchError(f"网络请求超时（{self._timeout}s），请稍后重试: {exc}") from exc
        except requests.ConnectionError as exc:
            raise SearchError(f"网络连接失败，无法访问百度搜索 API: {exc}") from exc
        except requests.RequestException as exc:
            raise SearchError(f"网络请求异常: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchError(
                f"API 返回了非 JSON 响应（HTTP {response.status_code}）: {exc}"
            ) from exc

        # 业务错误：响应中带 code 字段
        if "code" in data:
            raise SearchError(
                f"搜索 API 返回错误(code={data['code']}): {data.get('message', '无错误描述')}"
            )

        # HTTP 层非 2xx —— 也可能体现在 code 里，但这里兜底一次
        if not response.ok:
            request_id = data.get("request_id", "")
            msg = data.get("message", "") or response.reason
            raise SearchError(
                f"HTTP {response.status_code} {msg}{(' (' + request_id + ')') if request_id else ''}"
            )

        references = data.get("references") or []
        if not references:
            # 200 但无结果不视为错误，调用方会收到"(未找到相关结果)"的友好提示
            return []

        return references

    # ------------------------------------------------------------------
    # 结果格式化
    # ------------------------------------------------------------------
    @staticmethod
    def _format_references(references: list[dict[str, Any]], query: str) -> str:
        if not references:
            return (
                f"搜索词 '{query}' 未找到相关结果。"
                "可能原因：问题过于宽泛或需要换一种表述方式。"
            )

        lines = [f"搜索词: {query}", f"找到 {len(references)} 条结果:", ""]
        for i, ref in enumerate(references, start=1):
            title = (ref.get("title") or "(无标题)").strip()
            url = (ref.get("url") or "").strip()
            website = (ref.get("website") or "").strip()
            date = (ref.get("date") or "").strip()
            snippet = (ref.get("content") or ref.get("snippet") or "").strip()

            header_parts = [f"[{i}]"]
            header_parts.append(title)
            if website:
                header_parts.append(f"· {website}")
            if date:
                header_parts.append(f"· {date}")
            lines.append(" ".join(header_parts))
            if url:
                lines.append(f"  URL: {url}")
            if snippet:
                # snippet 可能很长，截断到 ~300 字符避免撑爆上下文
                preview = snippet if len(snippet) <= 300 else snippet[:300] + "..."
                lines.append(f"  摘要: {preview}")
            lines.append("")  # 每条之间留空行

        return "\n".join(lines).rstrip()

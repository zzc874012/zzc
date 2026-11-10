"""MCP 客户端管理器：后台 asyncio loop 维护连接，同步 API 供 Agent 工具层调用。

支持的协议：
- streamableHttp：`type: streamableHttp`，必填 url，可选 headers / timeout_seconds
- stdio：`type: stdio`，必填 command / args，可选 env（默认继承当前）

每个 server 独立维持一个 ClientSession；连接失败在发现阶段被捕获，
注册工具列表中该 server 被跳过，其他 server 不受影响。

与同步工具层的桥接：
- 后台线程启动一个唯一的 asyncio event loop，长期驻留
- 连接、list_tools、call_tool 都通过 asyncio.run_coroutine_threadsafe 投递到该 loop
- 公共 API 同步返回或抛异常，保持与 Tool.handler 签名兼容
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 轻量 fallback：没装 MCP SDK 时，导入并在连接阶段统一报错，
# 保证启动过程不因 mcp 缺依赖而整体失败（经验：隔离异常源）。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 生产路径
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client
    MCP_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    MCP_AVAILABLE = False
    _IMPORT_ERROR = exc


MCPCONFIG_FILENAME = ".mcp.json"
_CALL_TIMEOUT_SECONDS = 30  # call_tool 默认超时


@dataclass
class RegisteredTool:
    """MCP Server 报告的某个工具，已经映射到 LLM 侧名称。"""

    llm_name: str        # 暴露给 LLM 的名字（可能带前缀）
    server_name: str     # 在 MCP Server 上的原生工具名
    description: str
    parameters: dict[str, Any]


@dataclass
class ServerInfo:
    """单个 MCP Server 的配置 + 已发现工具 + 运行态句柄。"""

    name: str
    config: dict[str, Any]
    tools: list[RegisteredTool] = field(default_factory=list)
    last_error: str | None = None  # 最近一次连接/调用失败的可行动摘要

    # 内部：loop 里持有的引用
    _session_ref: Any = None   # 由 _MCPBackend 持有并通过 weakref 传递


class MCPClientError(RuntimeError):
    """MCP 客户端异常：Server 未配置、连接失败、工具调用失败等。"""


# ===========================================================================
# 后台 event loop 后端
# ===========================================================================
class _MCPBackend:
    """在独立后台线程里跑一个 asyncio event loop，管理所有连接。

    每个 server 对应一个独立的 ClientSession；initialize / list_tools 是注册期，
    call_tool 是请求期。公共方法都从同步线程调用，通过
    asyncio.run_coroutine_threadsafe 投递到后台 loop。
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._sessions: dict[str, Any] = {}   # server_name -> ClientSession
        self._connections: dict[str, Any] = {}  # server_name -> (r,w) async context guard
        self._lock = threading.Lock()
        self._start()

    # --- loop 生命周期 ------------------------------------------------------
    def _start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mcp-backend", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        if not self._loop:
            raise MCPClientError("MCP 后台 event loop 启动失败")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()
            self._loop = None

    def submit(self, coro: Any, timeout: float = _CALL_TIMEOUT_SECONDS) -> Any:
        """从同步线程投递一个协程到后台 loop 并等待结果。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            raise MCPClientError("MCP 后台 event loop 已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except asyncio.TimeoutError:
            fut.cancel()
            raise MCPClientError(f"MCP 调用超时（{timeout}s）")
        except BaseException as exc:  # noqa: BLE001
            # 经验：ExceptionGroup 包装需要展开，拿到最内层可行动摘要
            raise MCPClientError(_summarize_exception(exc)) from exc

    # --- server 连接 / 断开 --------------------------------------------------
    def connect(self, server_name: str, config: dict[str, Any]) -> None:
        """在后台 loop 里建立 stdio / streamable_http 连接并 initialize。"""
        self.submit(self._a_connect(server_name, config), timeout=15)

    def disconnect(self, server_name: str) -> None:
        self.submit(self._a_disconnect(server_name), timeout=5)

    # --- 工具发现 / 调用 ----------------------------------------------------
    def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        """返回 MCP Server 原生工具列表（schema + name + description）。"""
        result = self.submit(self._a_list_tools(server_name))
        out: list[dict[str, Any]] = []
        for t in getattr(result, "tools", []) or []:
            schema = getattr(t, "input_schema", None)
            # MCP input_schema 通常已是 JSON Schema；若是 dict 直接用，否则 to JSON Schema
            schema_dict = schema if isinstance(schema, dict) else {}
            out.append({
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", "") or "",
                "parameters": schema_dict,
            })
        return out

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用某个 server 上的工具，把结果序列化成适合 LLM 接收的纯文本。"""
        result = self.submit(self._a_call_tool(server_name, tool_name, arguments))
        if getattr(result, "is_error", False):
            raise MCPClientError(_render_result_content(getattr(result, "content", [])) or "工具执行失败")
        return _render_result_content(getattr(result, "content", []))

    # --- async 侧 ------------------------------------------------------------
    async def _a_connect(self, server_name: str, config: dict[str, Any]) -> None:
        if server_name in self._sessions:
            await self._a_disconnect(server_name)
        transport = config.get("type", "").lower()
        if transport in ("streamablehttp", "streamable_http", "http"):
            session, open_ctx = await self._connect_streamable_http(config)
        elif transport == "stdio":
            session, open_ctx = await self._connect_stdio(config)
        else:
            raise MCPClientError(f"不支持的 MCP 传输类型: {transport!r}")
        self._sessions[server_name] = session
        self._connections[server_name] = open_ctx

    async def _connect_streamable_http(self, config: dict[str, Any]) -> tuple[Any, Any]:
        if streamable_http_client is None:
            raise MCPClientError(f"MCP SDK 未安装: {_IMPORT_ERROR}")
        url = config.get("url")
        if not url:
            raise MCPClientError("streamableHttp server 必须配置 'url'")
        # http_client 可选：允许用户注入自定义（含 headers）httpx.AsyncClient
        import httpx
        headers = config.get("headers")
        http_kwargs: dict[str, Any] = {}
        if headers:
            http_kwargs["headers"] = headers
        timeout_val = config.get("timeout_seconds")
        if timeout_val:
            http_kwargs["timeout"] = float(timeout_val)
        http_client = httpx.AsyncClient(**http_kwargs) if http_kwargs else None

        cm = streamable_http_client(url, http_client=http_client)
        streams = await cm.__aenter__()
        session = ClientSession(streams[0], streams[1])
        await session.__aenter__()
        await session.initialize()
        return session, cm

    async def _connect_stdio(self, config: dict[str, Any]) -> tuple[Any, Any]:
        if stdio_client is None or StdioServerParameters is None:
            raise MCPClientError(f"MCP SDK 未安装: {_IMPORT_ERROR}")
        command = config.get("command")
        args = config.get("args", [])
        if not command:
            raise MCPClientError("stdio server 必须配置 'command'")
        sp = StdioServerParameters(command=command, args=args)
        env = config.get("env")
        if isinstance(env, dict):
            sp = StdioServerParameters(command=command, args=args, env={**os.environ, **env})
        cm = stdio_client(sp)
        streams = await cm.__aenter__()
        session = ClientSession(streams[0], streams[1])
        await session.__aenter__()
        await session.initialize()
        return session, cm

    async def _a_disconnect(self, server_name: str) -> None:
        session = self._sessions.pop(server_name, None)
        open_ctx = self._connections.pop(server_name, None)
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if open_ctx is not None:
            try:
                await open_ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    async def _a_list_tools(self, server_name: str) -> Any:
        session = self._sessions.get(server_name)
        if session is None:
            raise MCPClientError(f"Server '{server_name}' 未连接")
        return await session.list_tools()

    async def _a_call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        session = self._sessions.get(server_name)
        if session is None:
            raise MCPClientError(f"Server '{server_name}' 未连接")
        return await session.call_tool(tool_name, arguments or {})

    def close(self) -> None:
        """停止 loop 并断开所有 server；进程退出时由 daemon 线程自然回收。"""
        for name in list(self._sessions):
            try:
                self.disconnect(name)
            except MCPClientError:
                pass
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)


# ===========================================================================
# 公开的管理器
# ===========================================================================
class MCPClientManager:
    """配置驱动的 MCP 客户端管理器：发现、连接、暴露工具、调用。"""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.backend = _MCPBackend()
        self.servers: dict[str, ServerInfo] = {}
        self._server_tools: dict[str, str] = {}  # llm_tool_name -> server_name

    # ---------- 配置加载 ------------------------------------------------------
    @classmethod
    def load(cls, project_root: Path) -> "MCPClientManager":
        manager = cls(project_root)
        config_path = manager.project_root / MCPCONFIG_FILENAME
        if not config_path.is_file():
            return manager  # 无配置即无 MCP server，不是错误
        import json
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPClientError(f"读取 {config_path} 失败: {exc}") from exc
        servers = data.get("servers") or data.get("mcpServers") or {}
        if not isinstance(servers, dict):
            raise MCPClientError(f"{config_path} 的 'servers' / 'mcpServers' 必须是对象")
        manager._configure(servers)
        return manager

    def _configure(self, servers: dict[str, Any]) -> None:
        """逐个 server 连接，失败时记录 last_error，继续下一个。"""
        for name, cfg in servers.items():
            # 清理可能被引号/反引号包裹的 url 等字符串
            if isinstance(cfg, dict):
                cfg = {k: _clean_string(v) for k, v in cfg.items()}
            if not isinstance(cfg, dict):
                self.servers[name] = ServerInfo(name=name, config={},
                                                last_error="配置必须是对象，跳过")
                continue
            info = ServerInfo(name=name, config=cfg)
            self.servers[name] = info
            try:
                self.backend.connect(name, cfg)
                mcp_tools = self.backend.list_tools(name)
            except MCPClientError as exc:
                info.last_error = str(exc)
                continue
            for mcp_tool in mcp_tools:
                registered = self._register_tool(name, mcp_tool)
                info.tools.append(registered)

    def _register_tool(self, server_name: str, mcp_tool: dict[str, Any]) -> RegisteredTool:
        """确定 LLM 侧暴露名：优先用原生名，冲突时加 server 前缀。"""
        native = mcp_tool["name"]
        existing_server = self._server_tools.get(native)
        if existing_server is None:
            llm_name = native
        elif existing_server != server_name:
            llm_name = f"{_safe_identifier(server_name)}__{native}"
        else:
            # 同 server 重复 list 属于正常；保留第一次映射即可
            llm_name = native if self._server_tools.get(native) == server_name else native
        self._server_tools[llm_name] = server_name
        return RegisteredTool(
            llm_name=llm_name,
            server_name=native,
            description=mcp_tool["description"],
            parameters=_ensure_json_schema(mcp_tool["parameters"], native),
        )

    # ---------- 工具访问 ------------------------------------------------------
    def all_tools(self) -> list[RegisteredTool]:
        out: list[RegisteredTool] = []
        for info in self.servers.values():
            out.extend(info.tools)
        return out

    def call(self, llm_tool_name: str, arguments: dict[str, Any]) -> str:
        server_name = self._server_tools.get(llm_tool_name)
        if server_name is None:
            raise MCPClientError(f"未知 MCP 工具: {llm_tool_name}")
        info = self.servers.get(server_name)
        if info is None:
            raise MCPClientError(f"MCP server 配置丢失: {server_name}")
        for t in info.tools:
            if t.llm_name == llm_tool_name:
                break
        else:
            raise MCPClientError(f"MCP 工具映射丢失: {llm_tool_name}")
        try:
            return self.backend.call_tool(server_name, t.server_name, arguments)
        except MCPClientError as exc:
            info.last_error = str(exc)
            raise

    def tool_schemas(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in self.all_tools():
            out.append({
                "type": "function",
                "function": {
                    "name": t.llm_name,
                    "description": (
                        f"【MCP Server: {_server_label(self.servers, _server_of(self._server_tools, t.llm_name))}】"
                        f"{t.description}"
                    ),
                    "parameters": t.parameters,
                },
            })
        return out


# ===========================================================================
# 工具函数
# ===========================================================================
def _summarize_exception(exc: BaseException) -> str:
    """把 ExceptionGroup / CancelledError 等不可读异常展开成一行可行动摘要。"""
    msgs: list[str] = []
    stack = [exc]
    seen = set()
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        name = cur.__class__.__name__
        msg = str(cur).strip()
        if msg:
            msgs.append(f"{name}: {msg}")
        if hasattr(cur, "exceptions"):
            for sub in cur.exceptions:
                stack.append(sub)
        if cur.__cause__:
            stack.append(cur.__cause__)
    # 去重但保留顺序
    out: list[str] = []
    for m in msgs:
        if m not in out:
            out.append(m)
    return "; ".join(out[:6]) or exc.__class__.__name__


def _render_result_content(content: list[Any]) -> str:
    """把 CallToolResult.content 里的 TextContent / ImageContent / EmbeddedResource 转成纯文本。"""
    pieces: list[str] = []
    for item in content or []:
        item_type = getattr(item, "type", None)
        if item_type == "text":
            pieces.append(getattr(item, "text", "") or "")
        elif item_type == "image":
            mimetype = getattr(item, "mimeType", "image")
            pieces.append(f"[图片内容 ({mimetype})]")
        elif item_type == "resource":
            url = getattr(getattr(item, "resource", None), "uri", "")
            mime = getattr(item, "mimeType", "resource")
            pieces.append(f"[资源 {mime}: {url}]")
        else:
            pieces.append(repr(item))
    text = "\n".join(pieces).strip()
    return text or "(空结果)"


def _ensure_json_schema(raw: Any, name_hint: str) -> dict[str, Any]:
    """把 MCP 原生 input_schema 规整成 JSON Schema（LLM 能接受的最小结构）。"""
    if isinstance(raw, dict) and "type" in raw:
        out = dict(raw)
        out.setdefault("properties", {})
        return out
    return {"type": "object", "properties": {},
            "description": f"（MCP 工具 {name_hint} 未提供标准 JSON Schema）"}


def _safe_identifier(name: str) -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    return cleaned or "server"


def _clean_string(value: Any) -> Any:
    """去掉 url 等字符串首尾被意外包裹的反引号、单双引号。"""
    if not isinstance(value, str):
        return value
    s = value.strip()
    while s and s[0] in "`\"'" and s[-1] in "`\"'":
        s = s[1:-1].strip()
    return s


def _server_of(map_: dict[str, str], tool: str) -> str:
    return map_.get(tool, "")


def _server_label(servers: dict[str, ServerInfo], name: str) -> str:
    info = servers.get(name)
    return f"{name}({info.config.get('type','?')})" if info else name

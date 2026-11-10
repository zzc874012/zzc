# zAgent — 核心与界面分离的 LLM 文件操作智能体

由 `Practice02/file_ops.py` 重构而来：保留原有 10 个文件操作工具与 OpenAI 兼容流式调用，
把「业务核心」与「界面表现」拆成独立层次，当前提供 Textual TUI 与命令行两种界面，
并为 Web / Desktop 等后续界面预留统一的事件契约。

## 快速开始

```powershell
cd zAgent
python -m pip install -r requirements.txt
python main.py              # 默认启动 Textual TUI
python main.py --ui cli     # 命令行界面
```

配置文件 `config.ini`：

| 段 | 配置项 | 说明 |
|---|---|---|
| `[llm]` | `api_key` | SiliconFlow API 密钥 |
| `[llm]` | `base_url` | API 地址，如 `https://api.siliconflow.cn/v1` |
| `[llm]` | `model` | 支持工具调用的模型 |
| `[agent]` | `max_tool_rounds` | 单轮对话最多工具调用轮数，默认 8 |

命令行参数：

```powershell
python main.py --config D:\my\config.ini --workspace D:\work --max-tool-rounds 4
```

`--workspace` 指定允许文件操作的目录，默认当前目录；所有工具路径都会被限制在该目录内。

### TUI 快捷键

| 按键 | 作用 |
|---|---|
| `Enter` | 发送消息 |
| `Esc` | 取消当前轮流式输出 |
| `Ctrl+L` | 清空对话与上下文 |
| `Ctrl+Q` | 退出 |

### CLI

输入 `exit` 或 `quit` 结束，退出时打印完整对话记录（含工具调用与结果）。

## 目录结构

```
zAgent/
├── main.py                     # 启动入口
├── config.ini                  # 模型与 Agent 配置
├── requirements.txt
├── README.md
├── docs/
│   └── architecture.mmd        # Mermaid 架构图
└── zAgent/
    ├── bootstrap.py            # 参数解析 + 选择界面实现
    ├── __main__.py             # 支持 python -m zAgent
    ├── core/                   # 业务核心层（零界面依赖）
    │   ├── config.py           #   配置加载与校验
    │   ├── messages.py         #   OpenAI 消息构造
    │   ├── events.py           #   事件契约 + EventListener 协议
    │   ├── agent.py            #   对话循环、工具编排、取消、事件发布
    │   ├── llm/
    │   │   ├── base.py         #   LLMClient 协议、StreamChunk
    │   │   └── openai_compat.py#   SSE 流式客户端
    │   └── tools/
    │       ├── base.py         #   Tool / ToolRegistry / ToolResult
    │       └── file_ops.py     #   Workspace 路径沙箱 + 10 个工具
    └── interfaces/             # 界面适配层
        ├── cli.py              #   ConsoleListener + REPL
        └── tui/
            ├── app.py          #   Textual App（事件桥接、快捷键）
            ├── widgets.py      #   ChatLog / MessageBubble / StatusBar
            ├── app.tcss        #   界面样式
            └── __init__.py     #   run_tui()
```

## 分层约定

```
main.py / bootstrap.py  →  interfaces/*  →  core（只通过 core.__init__ 的公开 API）
core/*                  →  标准库 + requests，绝不 import textual
```

- **core 不知道界面存在**：不打印、不读键盘、不处理布局，只发事件。
- **interfaces 不写业务**：不拼装请求、不判断轮数、不校验路径，只把事件渲染成视图。
- **界面可替换**：新增界面就是新增一个 `interfaces/xxx` 包，core 一行不改。

## 核心概念

### 事件契约（`core/events.py`）

| 事件 | 含义 |
|---|---|
| `TurnStarted(user_text)` | 一轮对话开始 |
| `TextDelta(text)` | 模型流式输出增量 |
| `ToolCallRequested(tool_call_id, name, arguments)` | 模型请求调用工具 |
| `ToolCallCompleted(tool_call_id, name, arguments, result, ok)` | 工具执行完成 |
| `TurnCompleted(assistant_text)` | 一轮正常结束 |
| `TurnAborted(reason)` | 被取消或达到工具轮数上限 |
| `ErrorRaised(message)` | 可展示的错误，不崩溃进程 |

界面实现 `EventListener.on_event(event)` 即可接入，无需感知 Agent 内部实现。

### 工具（`core/tools/base.py`）

工具由 `Tool(名字, 描述, JSON Schema, handler)` 描述，注册到 `ToolRegistry`：

```python
registry = ToolRegistry([Tool(name="ping", description="健康检查",
                             parameters={"type": "object", "properties": {}},
                             handler=lambda: "pong")])
```

`registry.execute()` 会把所有异常统一转换为 `ToolResult(ok=False, output="错误: ...")`，
模型能读到错误并自行纠正参数，与原有 `exec_tool()` 行为一致。

### 会话与取消（`core/agent.py`）

```python
from zAgent.core import CallbackListener, build_agent, load_config

agent = build_agent(load_config())
agent.run_turn("列出当前目录", CallbackListener(print))
agent.cancel()   # 线程安全，可在界面线程调用
agent.reset()    # 清空上下文
```

`run_turn` 是同步阻塞调用，界面应在后台线程 / 线程池中执行它。

## 扩展指南

### 新增一个界面（Web / Desktop）

以 Web（FastAPI + SSE）为例，核心只需要一个事件监听器把事件写入线程安全队列：

```python
import asyncio
from dataclasses import asdict
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from zAgent.core import AgentEvent, EventListener, build_agent, load_config

class QueueListener(EventListener):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop, self._queue = loop, queue

    def on_event(self, event: AgentEvent) -> None:
        # 从工作线程安全地投递到事件循环
        self._loop.call_soon_threadsafe(self._queue.put_nowait, asdict(event))

app = FastAPI()
agent = build_agent(load_config())

@app.post("/chat")
async def chat(body: dict):
    queue: asyncio.Queue = asyncio.Queue()
    listener = QueueListener(asyncio.get_running_loop(), queue)

    async def events():
        await asyncio.to_thread(agent.run_turn, body["text"], listener)
        await queue.put({"event": "end"})
        while not queue.empty():
            yield f"data: {queue.get_nowait()}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
```

要点：

1. 用 `build_agent(config)` 复用同一套 core；
2. 在后台线程调用 `run_turn`，监听器把事件切回 UI 主线程（TUI 里用 Textual 的
   `call_from_thread`，Web 里用 `loop.call_soon_threadsafe`，桌面端可发 Qt 信号）；
3. 取消调用 `agent.cancel()`，清空会话调用 `agent.reset()`。

Desktop（PySide / Tkinter / pywebview）同理：把 `EventListener` 实现为向窗口发信号即可。

### 新增工具

1. 在 `core/tools/` 下实现一个 `Toolset` 类，返回若干 `Tool`；
2. 在 `Agent` 组装处把工具注册进 `ToolRegistry`（多个工具组可合并注册）；
3. 不需要改事件、界面或 LLM 客户端。

### 替换模型供应商

实现 `core/llm/base.py` 的 `LLMClient.stream_chat()`，产出 `StreamChunk` 即可；
Agent 只依赖协议，不关心是 OpenAI / Anthropic 还是本地模型。

## 安全设计

- **路径沙箱**：`Workspace.resolve()` 将相对路径解析到工作目录内，并拒绝任何越界路径
  （`..`、其他盘符、绝对路径越界）；工作目录本身禁止删除。
- **异常反馈**：工具异常统一转为错误文本回传给模型，不中断会话。
- **调用上限**：`max_tool_rounds`（默认 8）防止模型陷入工具调用死循环。
- **可取消**：`Agent.cancel()` 让界面可以中止长时间输出。
- **纯文本限制**：`readFile` / `editFile` 仅处理 UTF-8 纯文本，二进制文件返回提示。

## 与 Practice02 的对应关系

| Practice02 | zAgent |
|---|---|
| `file_ops.py` 顶层 10 个函数 | `core/tools/file_ops.py` → `FileOpsToolset` |
| `safe_path()` + `WORKSPACE` | `Workspace` 类 |
| `exec_tool()` | `ToolRegistry.execute()` |
| `TOOLS` JSON Schema 列表 | `Tool.schema()` 自动生成 |
| `chat()` 中 SSE 解析与递归 | `core/llm/openai_compat.py` + `Agent.run_turn()` |
| `print()` 直接输出 | `AgentEvent` → 界面渲染 |
| `input()` 主循环 | `interfaces/cli.py` / `interfaces/tui/app.py` |
| `MAX_TOOL_ROUNDS` | `config.ini` 的 `[agent] max_tool_rounds` |

## 架构图

见 [docs/architecture.mmd](docs/architecture.mmd)，可使用 mermaid.live、Typora 或
VSCode Mermaid 插件渲染。

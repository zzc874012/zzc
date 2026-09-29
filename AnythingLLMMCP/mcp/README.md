# AnythingLLM MCP Server

通过 MCP 2026-07-28 Streamable HTTP 协议暴露本机 AnythingLLM 第一个工作区的 AI 问答能力。

## 前置条件

- Python >= 3.10
- 本机已安装并运行 [AnythingLLM](https://docs.anythingllm.com/)（默认端口 3001）
- AnythingLLM 中至少有一个工作区

## 安装依赖

```bash
cd c:\Users\Lenovo\Desktop\MCP
pip install -e .
```

或直接安装依赖：

```bash
pip install "mcp==2.2.0" "httpx>=0.27" "uvicorn>=0.30"
```

## 启动 MCP 服务

```bash
cd c:\Users\Lenovo\Desktop\MCP
python server.py
```

启动后服务监听 `http://127.0.0.1:8000/mcp`，终端输出：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 环境变量（可选）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANYTHINGLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 服务地址 |
| `ANYTHINGLLM_API_KEY` | 内置默认 Key | AnythingLLM API 密钥 |
| `MCP_HOST` | `127.0.0.1` | MCP 服务绑定地址 |
| `MCP_PORT` | `8000` | MCP 服务端口 |

## 停止 MCP 服务

在运行 `python server.py` 的终端中按 `Ctrl+C` 即可停止。

如果服务在后台运行，可通过任务管理器结束 `python.exe` 进程，或使用：

```bash
# 查找占用 8000 端口的进程
netstat -ano | findstr :8000
# 终止进程（替换 <PID> 为上一步查到的 PID）
taskkill /PID <PID> /F
```

## 项目级 MCP 配置

在项目根目录创建 `.mcp.json` 即可在 Trae IDE 中使用：

```json
{
  "mcpServers": {
    "anythingllm": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

配置后，Trae IDE 的 AI 助手可自动调用 `chat_with_first_workspace` 工具进行知识库问答。

## 可用工具

| 工具名 | 参数 | 说明 |
|---|---|---|
| `chat_with_first_workspace` | `message` (str) | 向第一个工作区提问，返回 AI 答案和引用来源 |

## 文件结构

```
c:\Users\Lenovo\Desktop\MCP\
├── pyproject.toml   # 依赖声明
├── server.py        # 全部逻辑（配置 + 客户端 + MCP 工具 + 入口）
└── README.md        # 本文件
```

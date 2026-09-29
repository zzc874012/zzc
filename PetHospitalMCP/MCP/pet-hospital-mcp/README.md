# Pet Hospital MCP

一个独立的 **无状态 MCP 服务**，把现有 Go 宠物医院 REST API（`GET /api/v1/pets`）
通过 Model Context Protocol 暴露给 AI Agent。

本项目**只使用 SDK 2.x 的 `MCPServer`**，不包含任何 SDK 1.x `FastMCP` 代码，
也不实现旧协议的 `initialize` 握手、`Mcp-Session-Id`、会话存储或有状态 SSE 恢复。

## 技术规格

| 项目 | 值 |
| --- | --- |
| Python | 3.11+（开发验证环境：3.13） |
| MCP SDK | **`mcp==2.0.0`**（官方 Python SDK，固定版本） |
| MCP 协议版本 | **`2026-07-28`**（`mcp.types.LATEST_PROTOCOL_VERSION`） |
| 服务端 API | `mcp.server.MCPServer`（**非** `FastMCP`） |
| 传输 | Streamable HTTP，**无状态模式**（`stateless_http=True`） |
| HTTP 框架 | Starlette（由 SDK 装配）+ uvicorn |
| 上游调用 | `httpx`（异步，超时 + 有限重试） |
| 数据模型 / 校验 | Pydantic v2 |

## 目录结构

```text
pet-hospital-mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py          # 版本常量
│       ├── __main__.py          # python -m pet_hospital_mcp 启动入口
│       ├── config.py            # 环境变量配置 + 后端枚举常量
│       ├── server.py            # MCPServer、list_pets 注册、/health、ASGI 装配
│       ├── rest_client.py       # httpx 异步客户端（超时/重试/错误归一化）
│       ├── errors.py            # 6 种错误码 + 统一错误信封
│       ├── logging_config.py    # JSON 结构化日志 + PII 递归脱敏
│       └── tools/
│           ├── __init__.py
│           └── list_pets.py     # 输入/输出 Pydantic 模型 + 工具实现
└── tests/
    ├── conftest.py
    ├── test_list_pets_normal.py
    ├── test_list_pets_validation.py
    ├── test_list_pets_backend_errors.py
    ├── test_list_pets_timeout.py
    ├── test_list_pets_invalid_response.py
    ├── test_tool_registration.py
    ├── test_stateless_http.py
    └── test_logging.py
```

## 安装

需要 Python 3.11+。在项目目录 `pet-hospital-mcp/` 下：

```bash
python -m pip install -e ".[test]"
```

或仅安装运行依赖：

```bash
python -m pip install mcp==2.0.0 httpx pydantic uvicorn starlette
```

> 未安装包也可直接运行：测试与 `python -m` 入口会自动把 `src/` 加入路径
> （见 `tests/conftest.py` 与以 `src/` 布局运行的方式）。推荐正式使用时仍执行
> `pip install -e .`。

## 前置条件：先启动 Go REST API

MCP 服务**不包含业务逻辑**，所有数据来自 Go 宠物医院服务。启动 MCP 前必须先启动它。

使用仓库内的预编译发行版（Windows）：

```powershell
cd ..\windows
.\pethospital.exe
```

默认监听 `http://127.0.0.1:8080`，启动后可验证：

```bash
curl "http://127.0.0.1:8080/api/v1/pets?pageSize=1"
```

应返回 Go 的统一信封：

```json
{"code":200,"message":"ok","data":{"items":[...],"total":387,...},"time":"..."}
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址（默认仅本机） |
| `MCP_PORT` | `8000` | MCP 服务监听端口 |
| `MCP_PATH` | `/mcp` | MCP Streamable HTTP 端点路径 |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | 上游 Go REST API 地址 |
| `BACKEND_TIMEOUT_SECONDS` | `5.0` | 单次上游请求超时（秒） |
| `BACKEND_RETRIES` | `2` | 瞬时失败的重试次数（总尝试 = 重试 + 1） |
| `BACKEND_RETRY_BACKOFF_SECONDS` | `0.25` | 重试间隔（秒） |

## 启动 MCP 服务

```bash
python -m pet_hospital_mcp
```

或安装后使用脚本入口：

```bash
pet-hospital-mcp
```

自定义端口与上游示例（PowerShell）：

```powershell
$env:MCP_PORT="9000"
$env:PET_HOSPITAL_BASE_URL="http://127.0.0.1:8080"
python -m pet_hospital_mcp
```

启动后：

- MCP 端点：`http://127.0.0.1:8000/mcp`
- 健康检查：`http://127.0.0.1:8000/health`

## /health 示例

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "service": "pet-hospital-mcp",
  "version": "0.1.0",
  "mcp_sdk_version": "2.0.0",
  "mcp_protocol_version": "2026-07-28"
}
```

## 工具：`list_pets`

对应 `GET /api/v1/pets`。工具名为 `snake_case`，**仅**支持后端已有的 14 个查询参数，
没有任何适配器私有参数：

`q`、`name`、`ownerName`、`ownerPhone`、`species`、`doctor`、`disease`、`status`、
`min`、`max`、`sortBy`、`order`、`page`、`pageSize`

校验规则：

- `species`：`犬 / 猫 / 兔 / 鸟 / 仓鼠 / 爬宠 / 其他`
- `status`：`待就诊 / 就诊中 / 住院中 / 已康复 / 慢性病随访`
- `sortBy`：`id, name, ownerName, species, doctor, disease, status, totalCost, visitCount, createdAt, updatedAt`
- `order`：`asc / desc`
- `page >= 1`；`1 <= pageSize <= 500`
- `min`、`max` 非负且为有限数，`min <= max`
- 拒绝未知字段、NaN / Infinity、类型错误的输入

成功输出对应 Go 响应信封中的 `data`：
`items`、`total`、`page`、`pageSize`、`totalPages`、`totalCost`。
`items[*].records` 与 `items[*].charges` 兼容 `null` 或数组。

> **SDK 2.x 调用约定**：单个 Pydantic 模型参数在 JSON Schema 中嵌套为 `input`
> 属性，因此调用参数形如 `{"input": {...}}`。

### SDK 2.x 客户端调用示例

```python
import asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with Client(
        streamable_http_client("http://127.0.0.1:8000/mcp")
    ) as client:
        # 发现工具（2026-07-28：discover 流程，无 initialize 握手）
        tools = await client.list_tools()
        print([t.name for t in tools.tools])  # ['list_pets']

        # 查询：猫，按总花费降序，第 1 页，每页 3 条
        result = await client.call_tool(
            "list_pets",
            {"input": {
                "species": "猫",
                "sortBy": "totalCost",
                "order": "desc",
                "page": 1,
                "pageSize": 3,
            }},
        )
        print(result.is_error)            # False
        print(result.structured_content["total"])

asyncio.run(main())
```

### 错误结构

所有失败都返回统一信封（`result.is_error = true`，信封在 `content` 文本中）：

```json
{
  "error": {
    "code": "BACKEND_TIMEOUT",
    "message": "Backend timed out: TimeoutException",
    "details": {"url": "http://127.0.0.1:8080/api/v1/pets"}
  }
}
```

错误码：`VALIDATION_ERROR`、`BACKEND_TIMEOUT`、`BACKEND_UNAVAILABLE`、
`BACKEND_API_ERROR`、`BACKEND_INVALID_RESPONSE`、`INTERNAL_ERROR`。
HTTPX / Pydantic / Python 堆栈不会原样暴露给 MCP 客户端。

### MCP Inspector 验证

使用与 SDK 2.x 兼容的 MCP Inspector，选择 Streamable HTTP，URL 填：

```text
http://127.0.0.1:8000/mcp
```

连接后应能直接看到 `list_pets`（无初始化/会话步骤），并可在面板中调用。

## 日志

标准库 logging 输出单行 JSON，至少包含：
`timestamp`、`tool_name`、`params`、`status`、`duration_ms`。

`ownerPhone`、`ownerAddr`、`chipNo`（含 snake_case 写法）在任意嵌套层级都会被
递归替换为 `***REDACTED***`。

## 运行测试

测试**不访问真实 Go 服务**：上游一律由 `httpx.MockTransport` 模拟，
无状态 HTTP 层在测试内启动真实 uvicorn（临时端口）。

```bash
cd pet-hospital-mcp
pytest -q
```

预期结果（实际运行输出）：

```text
........................................................................ 100%
55 passed
```

覆盖范围对应任务要求的 7 类：

1. 正常调用（路径 + 全部过滤/排序/分页参数转发、records/charges 为 null）
2. 输入参数校验失败（枚举/范围/NaN/Infinity/未知字段/类型错误）
3. 后端 4xx/5xx
4. 超时与连接异常（含有限重试次数断言）
5. 非法 JSON / 不符合数据模型的响应
6. 工具注册、工具名与 JSON Schema（input/output schema）
7. 无状态连接：无 `initialize`、无 `Mcp-Session-Id`、`/health`、HTTP 发现与调用、
   两次独立连接

## 阶段范围声明

本服务**仅实现 `list_pets` 一个工具**。阶段二的其他工具（如宠物详情、统计、
写操作等）尚未实现，后续扩展指引见 [UPGRADE_PROMPT.md](./UPGRADE_PROMPT.md)。

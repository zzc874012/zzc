# 阶段二升级提示（UPGRADE_PROMPT）

本文档说明在**不破坏阶段一契约**的前提下，如何为本 MCP 服务增加新工具。
阶段一仅交付 `list_pets`，阶段二再按需扩展。

## 设计约束（沿用阶段一）

- 固定 `mcp==2.0.0`，继续使用 `mcp.server.MCPServer`，禁止引入 `FastMCP`。
- 保持无状态 Streamable HTTP（`stateless_http=True`），不引入会话、`Mcp-Session-Id`、
  会话存储或 SSE 恢复。
- 不修改 Go 后端；新能力只能通过其 HTTP API 获得。
- 每个工具：Pydantic 定义输入/输出 → 复用 `PetHospitalClient` → 返回结构化结果；
  失败统一抛出携带 `{error:{code,message,details}}` 的 `ToolError`。

## 新增一个工具的步骤

1. **在 `src/pet_hospital_mcp/tools/` 新建模块**，例如 `get_pet.py`：
   - 定义输入模型（`extra="forbid"`，枚举值从 `config.py` 引用，与 Go 后端对齐）。
   - 定义输出模型（兼容上游 `null`/数组的真实 JSON 表现）。
   - 参考 `list_pets.py` 的模式：
     - 用 `Field(json_schema_extra=...)` 把输入模型 JSON Schema 注入 dict 入参，
       让函数体自行校验并返回统一的 `VALIDATION_ERROR` 信封；
     - 用 `ToolCallLogger` 包裹调用；
     - 通过 `PetHospitalClient` 发起 HTTP 调用；
     - 拆解 Go 信封 `{code,message,data,time}` 后再做输出模型校验。
2. **需要新上游端点时**，在 `rest_client.py` 的 `PetHospitalClient` 增加方法
   （如 `get_pet(pet_id)`），保持“超时/有限重试/错误码归一化”逻辑一致；
   不要在工具层直接使用 httpx。
3. **在 `server.py` 注册**：`mcp.add_tool(run_xxx_tool, name="xxx", description=...)`，
   工具名使用 `snake_case`。
4. **补测试**（`tests/test_<tool>.py`），沿用 `conftest.py` 的
   `httpx.MockTransport` 夹具，禁止访问真实 Go 服务；至少覆盖正常调用、
   参数校验、上游 4xx/5xx、超时/连接异常、非法响应。
5. 运行 `pytest -q`，确保全部通过后再交付。

## 错误码复用

新工具只在现有 6 种错误码内选择：

- 入参不合法 → `VALIDATION_ERROR`
- 上游超时 → `BACKEND_TIMEOUT`
- 连不上上游 → `BACKEND_UNAVAILABLE`
- 上游返回非 2xx 或业务错误码 → `BACKEND_API_ERROR`
- 上游 200 但响应不可解析/不符模型 → `BACKEND_INVALID_RESPONSE`
- 其他兜底 → `INTERNAL_ERROR`

如确需新错误码，先在 `errors.py` 的 `ALL_ERROR_CODES` 中登记，并更新 README。

## 日志与脱敏

- 每次工具调用输出含 `timestamp/tool_name/params/status/duration_ms` 的 JSON 日志。
- 新增的敏感字段需同步加入 `logging_config.SENSITIVE_KEYS`（camelCase + snake_case）。
- 不要把结果中的宠物/主人明细整包写入日志；需要摘要时参考 `_summarize()`。

## 配置

新的上游地址、超时、重试统一走 `config.py` 的环境变量，不要硬编码。

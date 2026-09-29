# AnythingLLM 文档上传 + 向量化嵌入 网页

## Context（背景与目标）
用户需要一个网页，调用 AnythingLLM（http://localhost:3001）的 API，向**第一个工作区**上传文档并**完成向量化嵌入**。用户给定的 Key：`YOUR_ANYTHINGLLM_API_KEY`。要求：尽量简单、不冗余、不过度设计。

## 关键事实（已通过 curl 实测验证）
1. **第一个工作区**：`GET /api/v1/workspaces` 返回 `workspaces[0]` →
   - name="我的工作区"
   - slug=`2ee30fa2-718c-47f5-a569-95af67485f44`
   （计划中仍动态取 `workspaces[0].slug`，避免硬编码失效。）
2. **CORS 全通**：服务器回显 Origin。`null`（file://）origin 同样返回 `ACAO: null`；预检 OPTIONS 返回 204，`Access-Control-Allow-Headers: authorization,content-type`，`Access-Control-Allow-Methods` 含 POST。
   → **可直接以 file:// 打开 HTML，无需本地服务器**。
3. **重要 API 语义**：`POST /api/v1/document/upload` 仅把文件存入系统文档库（custom-documents/），**本身不绑定工作区、不触发嵌入**。要"完成向量化嵌入"必须追加第二步：
   - `POST /api/v1/workspaces/{slug}/update-embeddings`，body `{ "adds": [<location>], "deletes": [] }`
   - `<location>` 来自上传响应中 `documents.localFiles[0].location`（形如 `custom-documents/xxx-uuid-filename.json`）。

   为达成用户明示的"完成向量化嵌入"目标，本计划采用 **2 步流程**：上传 → 嵌入。

## 实现方案
单文件自包含 HTML（无构建、无依赖、无外部库）。

**文件**：`c:\Users\Lenovo\Desktop\MCP\upload.html`

**页面元素**（极简）：
- `<input type="file" id="file">`（单文件）
- `<button id="btn">上传并嵌入</button>`
- `<pre id="log">`（状态/结果输出）

**JS 逻辑**（fetch）：
1. 常量：`API_BASE="http://localhost:3001/api/v1"`，`API_KEY="YOUR_ANYTHINGLLM_API_KEY"`，公共头 `Authorization: Bearer <key>`。
2. `GET /workspaces` → 取 `data.workspaces[0].slug`。
3. `POST /document/upload`，body = `FormData`（字段名 `file`，值为所选文件）→ 取 `data.documents.localFiles[0].location`。
4. `POST /workspaces/{slug}/update-embeddings`，JSON `{adds:[location], deletes:[]}` → 嵌入结果。
5. 每步把状态写入 `#log`；失败即中止并显示错误。

**简化取舍**：
- Key 内嵌为 JS 常量（本地工具，可接受；换取最少交互）。
- 单文件上传（用户措辞为单文档）。
- 不做进度条/多文件/样式美化。

## 验证方法
1. 用 `start "" "c:\Users\Lenovo\Desktop\MCP\upload.html"` 在默认浏览器打开。
2. 选一个 .txt 文件 → 点"上传并嵌入" → `#log` 依次显示：工作区 slug、上传 location、嵌入成功。
3. 回验：`GET /api/v1/workspaces/2ee30fa2-718c-47f5-a569-95af67485f44` 应能看到该文档已嵌入；或在工作区对话中能检索到该文档内容。

## 待修改/新增文件
- 新增：`c:\Users\Lenovo\Desktop\MCP\upload.html`（唯一文件）

## 执行末步
- 完成后用 `start` 命令打开该 HTML（满足"完成后在浏览器打开"）。

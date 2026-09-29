# LLM 项目集合

一组围绕 **大语言模型（LLM）应用** 与 **MCP（Model Context Protocol）** 的学习与实践项目。

## 目录结构

```
LLM/
├── AnythingLLMMCP/      # AnythingLLM MCP Server（Python）
├── AnythingLLMServer/   # AnythingLLM 文档上传工具
├── PetHospitalMCP/      # 宠物医院 MCP（Python MCP + Go REST API）
└── README.md
```

## 子项目简介

### 1. AnythingLLMMCP — AnythingLLM MCP Server

通过 MCP Streamable HTTP 协议暴露本机 [AnythingLLM](https://docs.anythingllm.com/) 第一个工作区的 AI 问答能力。

- **语言**：Python 3.10+
- **依赖**：`mcp==2.2.0`、`httpx`、`uvicorn`
- **入口**：`AnythingLLMMCP/mcp/server.py`
- **工具**：`chat_with_first_workspace(message)` — 向第一个工作区提问，返回 AI 答案和引用来源

详见 [AnythingLLMMCP/mcp/README.md](AnythingLLMMCP/mcp/README.md)。

### 2. AnythingLLMServer — 文档上传工具

为 AnythingLLM 提供网页端的文档上传功能。

- **入口**：`AnythingLLMServer/MCP/upload.html`

### 3. PetHospitalMCP — 宠物医院 MCP

把 Go 编写的宠物医院 REST API 通过 MCP 协议暴露给 AI Agent，包含完整教学与测试代码。

#### 3a. Go REST API（`pet-hospital-mcp-teaching-main/`）

用 **Go 标准库** 编写的本地宠物医院管理系统，提供网页操作界面和完整的 REST 接口。

- 单文件数据库（append-only + CRC32 + 自动压实）
- 29 个接口：CRUD、批量、导出、统计等
- 网页界面内嵌进可执行文件，零第三方依赖

#### 3b. Python MCP Server（`pet-hospital-mcp/`）

无状态 MCP 服务，通过 Streamable HTTP 暴露 `list_pets` 工具。

- **MCP SDK**：`mcp==2.0.0`（官方 Python SDK）
- **协议版本**：`2026-07-28`
- **传输**：Streamable HTTP，无状态模式
- 完整测试套件（55 个用例，覆盖 7 类场景）

详见 [PetHospitalMCP/MCP/pet-hospital-mcp/README.md](PetHospitalMCP/MCP/pet-hospital-mcp/README.md)。

## 技术栈

| 领域 | 技术 |
| --- | --- |
| MCP 协议 | Model Context Protocol（Streamable HTTP，2026-07-28） |
| Python | 3.10+（MCP Server 开发） |
| Go | 1.22+（REST API 后端） |
| 数据库 | 自实现嵌入式存储引擎 |
| 测试 | pytest（httpx MockTransport + 真实 uvicorn） |

## 快速开始

### 启动 Go REST API

```powershell
cd PetHospitalMCP\MCP\windows
.\pethospital.exe
```

默认监听 `http://127.0.0.1:8080`。

### 启动宠物医院 MCP

```bash
cd PetHospitalMCP\MCP\pet-hospital-mcp
pip install -e ".[test]"
python -m pet_hospital_mcp
```

MCP 端点：`http://127.0.0.1:8000/mcp`

### 启动 AnythingLLM MCP

```bash
cd AnythingLLMMCP\mcp
pip install -e .
python server.py
```

## License

仅供学习与教学使用。

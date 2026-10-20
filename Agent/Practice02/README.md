# Practice02 — LLM Function Calling 实践

基于 OpenAI 兼容 API（SiliconFlow）的流式对话客户端，演示 Function Calling 工具调用机制。

## 目录结构

```
Practice02/
├── config.ini                 # 模型配置（api_key / base_url / model）
├── main.py                    # 练习 1：getDate 工具（调用本地 date 命令）
├── file_ops.py                # 练习 2：10 个本地文件操作工具
├── file_ops_architecture.mmd  # file_ops.py 架构图（Mermaid）
└── README.md
```

## 配置

编辑 `config.ini` 的 `[llm]` 段：

| 配置项 | 说明 |
|---|---|
| `api_key` | SiliconFlow API 密钥 |
| `base_url` | API 地址，如 `https://api.siliconflow.cn/v1` |
| `model` | 支持工具调用的模型名称 |

## 运行

```powershell
cd Practice02
python main.py       # 练习 1：getDate 工具
python file_ops.py   # 练习 2：文件操作工具
```

对话过程中输入 `exit` 或 `quit` 结束，退出时在终端输出完整对话记录（含工具调用过程）。

## 功能说明

### main.py — getDate 工具

- SSE 流式输出，多轮对话。
- `getDate` 工具：通过 `subprocess` 调用本地 `date /t & time /t` 命令获取当前日期时间（非 Python datetime），自动兼容 UTF-8/GBK 解码。

### file_ops.py — 文件操作工具组

| 工具 | 功能 |
|---|---|
| `getCwd` | 获取当前工作目录 |
| `listDir` | 列出指定目录下所有文件和子目录 |
| `getFileType` | 获取指定文件的类型（扩展名 / MIME） |
| `readFile` | 读取指定文件内容（仅纯文本格式） |
| `createFile` | 根据指定目录和文件名创建新文件 |
| `editFile` | 编辑指定文件内容（整体覆盖，仅纯文本） |
| `createDir` | 创建新目录（支持多级） |
| `removeDir` | 删除目录（支持非空，递归删除） |
| `removeFile` | 删除指定文件 |
| `rename` | 重命名文件或目录 |

## 安全设计（file_ops.py）

- **路径限制**：所有路径经 `safe_path()` 校验（`commonpath`），仅允许操作启动时锁定的工作目录 `WORKSPACE` 内的文件，越界路径（如 `../../`、其他盘符）直接拒绝。
- **异常反馈**：`exec_tool()` 将所有异常统一转换为错误字符串返回给模型，模型可据此纠正参数重试。
- **调用上限**：`MAX_TOOL_ROUNDS = 8`，防止模型陷入工具调用死循环。
- **保护工作目录**：禁止删除 `WORKSPACE` 本身。

## 架构图

`file_ops.py` 的代码架构见 [file_ops_architecture.mmd](file_ops_architecture.mmd)（Mermaid 纯文本格式），可使用 [mermaid.live](https://mermaid.live)、Typora 或 VSCode Mermaid 插件渲染查看。

import configparser
import json
import os
import shutil
import mimetypes
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config.ini"), encoding="utf-8")
API_KEY = config["llm"]["api_key"]
BASE_URL = config["llm"]["base_url"]
MODEL = config["llm"]["model"]

# 所有文件操作只允许在该工作目录内进行
WORKSPACE = os.path.abspath(os.getcwd())

TOOLS = [
    {"type": "function", "function": {
        "name": "getCwd",
        "description": "获取当前工作目录（所有文件操作只允许在该目录内进行）",
        "parameters": {"type": "object", "properties": {}}},
    },
    {"type": "function", "function": {
        "name": "listDir",
        "description": "列出指定目录下所有文件和子目录",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径，默认为当前工作目录"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "getFileType",
        "description": "获取指定文件的文件类型（目录 / 扩展名 / MIME 类型）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件或目录路径"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "readFile",
        "description": "获取指定文件的内容，仅支持纯文本格式（如 .txt/.py/.md/.json/.ini 等）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "createFile",
        "description": "根据指定的目录和文件名创建新文件，可写入初始纯文本内容",
        "parameters": {"type": "object", "properties": {
            "directory": {"type": "string", "description": "目标目录"},
            "filename": {"type": "string", "description": "新文件名"},
            "content": {"type": "string", "description": "文件初始内容，默认为空"}
        }, "required": ["directory", "filename"]}},
    },
    {"type": "function", "function": {
        "name": "editFile",
        "description": "编辑指定文件的内容（整体覆盖写入），仅支持纯文本格式",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "新的完整内容"}
        }, "required": ["path", "content"]}},
    },
    {"type": "function", "function": {
        "name": "createDir",
        "description": "创建新目录（支持一次创建多级目录）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "新目录路径"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "removeDir",
        "description": "删除指定目录，支持删除非空目录（递归删除目录下所有内容，谨慎使用）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "要删除的目录路径"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "removeFile",
        "description": "删除指定的文件",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "要删除的文件路径"}
        }, "required": ["path"]}},
    },
    {"type": "function", "function": {
        "name": "rename",
        "description": "重命名文件或目录（也可用于移动到同工作目录内的其他位置）",
        "parameters": {"type": "object", "properties": {
            "src": {"type": "string", "description": "原路径"},
            "dst": {"type": "string", "description": "新路径"}
        }, "required": ["src", "dst"]}},
    },
]


def safe_path(path):
    """解析路径并确保其位于工作目录内，防止越界操作"""
    p = os.path.abspath(path)
    try:
        inside = os.path.commonpath([WORKSPACE, p]) == WORKSPACE
    except ValueError:  # Windows 下跨盘符会抛 ValueError
        inside = False
    if not inside:
        raise PermissionError(f"路径 {path} 超出工作目录 {WORKSPACE}")
    return p


def getCwd():
    return WORKSPACE


def listDir(path="."):
    p = safe_path(path)
    if not os.path.isdir(p):
        raise NotADirectoryError(f"目录不存在: {path}")
    entries = sorted(os.listdir(p))
    if not entries:
        return "(空目录)"
    lines = []
    for name in entries:
        full = os.path.join(p, name)
        tag = "目录" if os.path.isdir(full) else "文件"
        lines.append(f"[{tag}] {name}")
    return "\n".join(lines)


def getFileType(path):
    p = safe_path(path)
    if os.path.isdir(p):
        return "目录"
    if not os.path.isfile(p):
        raise FileNotFoundError(f"文件不存在: {path}")
    ext = os.path.splitext(p)[1].lower().lstrip(".") or "无扩展名"
    mime = mimetypes.guess_type(p)[0] or "未知"
    return f"扩展名: .{ext} / MIME 类型: {mime}"


def readFile(path):
    p = safe_path(path)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"文件不存在: {path}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        return "错误: 该文件不是纯文本格式，仅支持纯文本文件"


def createFile(directory, filename, content=""):
    p = safe_path(os.path.join(directory, filename))
    if os.path.exists(p):
        raise FileExistsError(f"文件已存在: {p}")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已创建文件: {p}"


def editFile(path, content):
    p = safe_path(path)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已更新文件: {p}"


def createDir(path):
    p = safe_path(path)
    if os.path.exists(p):
        raise FileExistsError(f"目录已存在: {p}")
    os.makedirs(p)
    return f"已创建目录: {p}"


def removeDir(path):
    p = safe_path(path)
    if p == WORKSPACE:
        raise PermissionError("不能删除工作目录本身")
    if not os.path.isdir(p):
        raise NotADirectoryError(f"目录不存在: {path}")
    shutil.rmtree(p)
    return f"已删除目录: {p}"


def removeFile(path):
    p = safe_path(path)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"文件不存在: {path}")
    os.remove(p)
    return f"已删除文件: {p}"


def rename(src, dst):
    sp, dp = safe_path(src), safe_path(dst)
    if not os.path.exists(sp):
        raise FileNotFoundError(f"{src} 不存在")
    if os.path.exists(dp):
        raise FileExistsError(f"{dst} 已存在")
    shutil.move(sp, dp)
    return f"已重命名: {src} -> {dst}"


def exec_tool(name, args):
    """执行工具并返回字符串结果，任何异常都转换为错误信息反馈给模型"""
    try:
        if name == "getCwd":
            return getCwd()
        if name == "listDir":
            return listDir(args.get("path", "."))
        if name == "getFileType":
            return getFileType(args["path"])
        if name == "readFile":
            return readFile(args["path"])
        if name == "createFile":
            return createFile(args["directory"], args["filename"], args.get("content", ""))
        if name == "editFile":
            return editFile(args["path"], args["content"])
        if name == "createDir":
            return createDir(args["path"])
        if name == "removeDir":
            return removeDir(args["path"])
        if name == "removeFile":
            return removeFile(args["path"])
        if name == "rename":
            return rename(args["src"], args["dst"])
        return f"错误: 未知工具 {name}"
    except KeyError as e:
        return f"错误: 缺少参数 {e}"
    except Exception as e:
        return f"错误: {e}"


MAX_TOOL_ROUNDS = 8


def chat(messages, depth=0):
    resp = requests.post(
        f"{BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": MODEL, "messages": messages, "tools": TOOLS, "stream": True},
        stream=True,
    )
    if not resp.ok:
        print(f"HTTP {resp.status_code}: {resp.text}")
        return ""

    full = ""
    tool_calls = {}
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        delta = json.loads(data)["choices"][0]["delta"]

        content = delta.get("content")
        if content:
            full += content
            for ch in content:
                print(ch, end="", flush=True)

        for tc in delta.get("tool_calls") or []:
            slot = tool_calls.setdefault(
                tc["index"], {"id": "", "name": "", "arguments": ""}
            )
            if tc.get("id"):
                slot["id"] += tc["id"]
            if tc.get("function", {}).get("name"):
                slot["name"] += tc["function"]["name"]
            if tc.get("function", {}).get("arguments"):
                slot["arguments"] += tc["function"]["arguments"]

    # 模型请求调用工具
    if tool_calls:
        messages.append(
            {
                "role": "assistant",
                "content": full or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in (tool_calls[i] for i in sorted(tool_calls))
                ],
            }
        )
        for tc in (tool_calls[i] for i in sorted(tool_calls)):
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = exec_tool(tc["name"], args)
            print(f"\nTool({tc['name']} {tc['arguments']}) -> {result}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": result,
                }
            )
        if depth + 1 >= MAX_TOOL_ROUNDS:
            print("\n[工具调用轮数已达上限，停止]")
            return full
        print("AI: ", end="", flush=True)
        return chat(messages, depth + 1)

    print()
    return full


def show_history(messages):
    """将完整对话结果呈现在终端"""
    print("\n========== 对话记录 ==========")
    for msg in messages:
        role = msg["role"]
        if role == "user":
            print(f"You: {msg['content']}")
        elif role == "assistant":
            if msg.get("content"):
                print(f"AI: {msg['content']}")
            for tc in msg.get("tool_calls") or []:
                print(f"AI 请求调用工具: {tc['function']['name']}({tc['function']['arguments']})")
        elif role == "tool":
            print(f"Tool({msg.get('name')}): {msg['content']}")
    print("==============================\n")


if __name__ == "__main__":
    messages = []
    print(f"文件操作工作目录: {WORKSPACE}")
    print("输入 exit 或 quit 结束对话并查看完整对话记录。")
    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.strip().lower() in ("exit", "quit"):
            break
        messages.append({"role": "user", "content": prompt})
        print("AI: ", end="", flush=True)
        reply = chat(messages)
        if reply:
            messages.append({"role": "assistant", "content": reply})
    show_history(messages)

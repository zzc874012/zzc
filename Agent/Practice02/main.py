import configparser
import json
import subprocess
import requests

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")
API_KEY = config["llm"]["api_key"]
BASE_URL = config["llm"]["base_url"]
MODEL = config["llm"]["model"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def getDate():
    """通过调用本地 date/time 命令获取当前日期和时间"""
    result = subprocess.run("date /t & time /t", shell=True, capture_output=True)
    for enc in ("utf-8", "gbk"):
        try:
            return " ".join(result.stdout.decode(enc).split())
        except UnicodeDecodeError:
            continue
    return result.stdout.decode("utf-8", errors="replace")


def chat(messages):
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
            if tc["name"] == "getDate":
                result = getDate()
                # 工具结果直接显示在对话中
                print(f"\nTool(getDate): {result}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": result,
                    }
                )
        print("AI: ", end="", flush=True)
        return chat(messages)

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
                print(f"AI 请求调用工具: {tc['function']['name']}")
        elif role == "tool":
            print(f"Tool({msg.get('name', 'getDate')}): {msg['content']}")
    print("==============================\n")


if __name__ == "__main__":
    messages = []
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

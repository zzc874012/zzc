import configparser
import json
import requests

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")
API_KEY = config["llm"]["api_key"]
BASE_URL = config["llm"]["base_url"]
MODEL = config["llm"]["model"]


def chat(messages):
    resp = requests.post(
        f"{BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": MODEL, "messages": messages, "stream": True},
        stream=True,
    )
    if not resp.ok:
        print(f"HTTP {resp.status_code}: {resp.text}")
        return ""
    full = ""
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        delta = json.loads(data)["choices"][0]["delta"].get("content")
        if delta:
            full += delta
            for ch in delta:
                print(ch, end="", flush=True)
    print()
    return full


if __name__ == "__main__":
    messages = []
    while True:
        prompt = input("You: ")
        messages.append({"role": "user", "content": prompt})
        print("AI: ", end="", flush=True)
        reply = chat(messages)
        if reply:
            messages.append({"role": "assistant", "content": reply})

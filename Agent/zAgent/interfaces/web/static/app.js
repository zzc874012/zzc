/* zAgent Web 前端：消费 /api/chat 的 SSE 事件流，把 core 事件渲染成气泡。
 * 与 TUI 保持一致的语义：user / assistant / tool / warning / error / system。
 */
(function () {
  "use strict";

  var chatEl = document.getElementById("chat");
  var promptEl = document.getElementById("prompt");
  var statusEl = document.getElementById("status");
  var subtitleEl = document.getElementById("subtitle");
  var btnSend = document.getElementById("btn-send");
  var btnCancel = document.getElementById("btn-cancel");
  var btnReset = document.getElementById("btn-reset");

  var busy = false;
  var eventSource = null;
  var streamingBubble = null; // { box, body, text }
  var ROLE_TITLES = {
    user: "你",
    assistant: "AI",
    tool: "工具",
    warning: "提示",
    error: "错误",
    system: "系统"
  };

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status " + (kind || "info");
  }

  function setBusy(value) {
    busy = value;
    btnSend.hidden = value;
    btnCancel.hidden = !value;
    btnReset.disabled = value;
    promptEl.disabled = value;
    if (!value) {
      stopStream();
      promptEl.focus();
    }
  }

  function scrollToBottom() {
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function addBubble(role, text) {
    var box = document.createElement("div");
    box.className = "bubble " + role;

    var title = document.createElement("div");
    title.className = "bubble-title";
    title.textContent = ROLE_TITLES[role] || role;

    var body = document.createElement("div");
    body.className = "bubble-body";
    body.textContent = text || "";

    box.appendChild(title);
    box.appendChild(body);
    chatEl.appendChild(box);
    scrollToBottom();
    return { box: box, body: body, text: text || "" };
  }

  function finishStreaming() {
    if (streamingBubble) {
      streamingBubble.box.classList.remove("streaming");
      streamingBubble = null;
    }
  }

  function handleEvent(payload) {
    var kind = payload.kind;
    if (kind === "text_delta") {
      if (!streamingBubble) {
        streamingBubble = addBubble("assistant", "");
        streamingBubble.box.classList.add("streaming");
      }
      streamingBubble.text += payload.text;
      streamingBubble.body.textContent = streamingBubble.text;
      scrollToBottom();
      return;
    }

    // 其余事件都意味着当前流式气泡到此为止
    finishStreaming();

    if (kind === "tool_requested") {
      var args = JSON.stringify(payload.arguments);
      addBubble("tool", "调用 " + payload.name + "(" + args + ")");
      setStatus("执行工具 " + payload.name + "…", "busy");
    } else if (kind === "tool_completed") {
      addBubble("tool", (payload.ok ? "成功 " : "失败 ") + payload.name + " → " + payload.result);
      setStatus("思考中…", "busy");
    } else if (kind === "turn_completed") {
      setStatus("就绪", "info");
    } else if (kind === "turn_aborted") {
      addBubble("warning", payload.reason);
      setStatus("已中止", "error");
    } else if (kind === "error") {
      addBubble("error", payload.message);
      setStatus("出错", "error");
    }
  }

  function stopStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  async function send() {
    var text = promptEl.value.trim();
    if (!text || busy) return;

    promptEl.value = "";
    setBusy(true);
    addBubble("user", text);
    setStatus("思考中…", "busy");

    try {
      // 用 fetch + ReadableStream 手动解析 SSE，以便用 POST 发送 JSON 请求体
      var response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });

      if (!response.ok) {
        addBubble("error", "HTTP " + response.status + " " + (await response.text()));
        setStatus("出错", "error");
        setBusy(false);
        return;
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buffer = "";

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        buffer += decoder.decode(result.value, { stream: true });
        var frames = buffer.split("\n\n");
        buffer = frames.pop(); // 末尾可能是半帧，留给下一轮

        for (var i = 0; i < frames.length; i++) {
          var line = frames[i];
          if (!line.startsWith("data:")) continue; // 心跳帧以 : 开头
          try {
            handleEvent(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            // 单帧解析失败不应影响后续事件
          }
        }
      }
    } catch (err) {
      addBubble("error", String(err));
      setStatus("出错", "error");
    } finally {
      finishStreaming();
      setBusy(false);
    }
  }

  async function cancelTurn() {
    try {
      await fetch("/api/cancel", { method: "POST" });
      setStatus("正在取消…", "busy");
    } catch (e) {
      setStatus("取消失败", "error");
    }
  }

  async function resetChat() {
    var res = await fetch("/api/reset", { method: "POST" });
    if (res.ok) {
      chatEl.innerHTML = "";
      addBubble("system", "对话已清空。");
      setStatus("就绪", "info");
    } else {
      var data = await res.json().catch(() => ({}));
      setStatus(data.message || "无法清空", "error");
    }
  }

  btnSend.addEventListener("click", send);
  btnCancel.addEventListener("click", cancelTurn);
  btnReset.addEventListener("click", resetChat);

  promptEl.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  // 加载运行信息（模型 / 工作目录 / 工具数）
  fetch("/api/info")
    .then(function (r) { return r.json(); })
    .then(function (info) {
      subtitleEl.textContent =
        info.model + " · " + info.workspace + " · " + info.tools.length + " 个工具";
      addBubble(
        "system",
        "模型: " + info.model +
        "\n工作目录: " + info.workspace +
        "\n可用工具: " + info.tools.join(", ") +
        "\n最多 " + info.max_tool_rounds + " 轮工具调用"
      );
    })
    .catch(function () {
      subtitleEl.textContent = "信息加载失败";
    });
})();

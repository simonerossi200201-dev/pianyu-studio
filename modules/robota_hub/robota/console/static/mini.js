// Robota Mini Assistant Window Logic
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let ws = null;
let currentSid = null;
let isRecordingWf = false;
let isVoiceRecording = false;
let mediaRec = null;
let audioChunks = [];

// Call Mode (Hands-free duplex conversation)
let isCallMode = false;
let vadStream = null;
let vadAudioCtx = null;
let vadAnalyser = null;
let vadAnimId = null;
let vadSpeaking = false;
let vadSilenceStart = 0;
let callMediaRec = null;
let callChunks = [];
let isAiResponding = false;

// ---------------------------------------------------------------- WebSocket Chat
function connectWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/chat`);

  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      handleWsEvent(data);
    } catch (_) {}
  };

  ws.onclose = () => {
    setTimeout(connectWs, 1500);
  };
}

let activeAiRow = null;
let activeAiBubble = null;
let activeStepPill = null;
let activeAiText = "";

function mdInline(s) {
  // s is already HTML-escaped; formatting never spans lines
  return s
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^*])\*(?!\*)/g, "$1"); // drop stray single asterisks
}

function renderMarkdown(text) {
  if (!text) return "";
  const esc = String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const out = [];
  const parts = esc.split(/```\w*\n?([\s\S]*?)```/g); // odd indexes = code blocks
  parts.forEach((part, i) => {
    if (i % 2 === 1) { out.push(`<pre><code class="code-block">${part}</code></pre>`); return; }
    let blank = 0;
    for (const raw of part.split("\n")) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) { blank++; continue; }
      if (blank && out.length) out.push('<div class="md-gap"></div>');
      blank = 0;
      let m;
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) out.push('<hr class="md-hr">');
      else if ((m = line.match(/^\s*(#{1,6})\s+(.*)$/))) out.push(`<div class="md-h${Math.min(m[1].length, 3)}">${mdInline(m[2])}</div>`);
      else if ((m = line.match(/^\s*&gt;\s?(.*)$/))) out.push(`<div class="md-quote">${mdInline(m[1]) || "&nbsp;"}</div>`);
      else if ((m = line.match(/^(\s*)[-*+•]\s+(.*)$/))) {
        const lvl = Math.min(3, Math.floor(m[1].replace(/\t/g, "  ").length / 2));
        out.push(`<div class="md-li" style="margin-left:${lvl * 14}px"><span class="md-bullet">•</span>${mdInline(m[2])}</div>`);
      } else if ((m = line.match(/^(\s*)(\d+)[.)]\s+(.*)$/))) {
        const lvl = Math.min(3, Math.floor(m[1].length / 2));
        out.push(`<div class="md-li md-ol" style="margin-left:${lvl * 14}px"><span class="md-bullet">${m[2]}.</span>${mdInline(m[3])}</div>`);
      } else out.push(`<div class="md-p">${mdInline(line.trim())}</div>`);
    }
  });
  return out.join("");
}

function appendMsg(role, text, meta = "") {
  $("#welcomeCard")?.remove();
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.innerHTML = role === "user"
    ? text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")
    : renderMarkdown(text);

  const m = document.createElement("div");
  m.className = "msg-meta";
  m.textContent = meta || (new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));

  row.appendChild(bubble);
  row.appendChild(m);
  $("#messages").appendChild(row);
  if (role === "ai") addMsgActions(bubble, text);
  $("#chatBox").scrollTop = $("#chatBox").scrollHeight;
  return { row, bubble, meta: m };
}

// ---------------------------------------------------------------- per-reply actions (copy / read aloud)
const ICON_COPY = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICON_OK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
const ICON_SPEAK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/></svg>';
const ICON_STOP = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
let activeSpeakBtn = null;

function setSpeakBtn(btn, on) {
  if (!btn) return;
  btn.classList.toggle("on", on);
  btn.innerHTML = on ? ICON_STOP : ICON_SPEAK;
  btn.title = on ? "停止朗读" : "朗读";
}

function speakWithButton(text, btn, onEnd) {
  if (activeSpeakBtn && activeSpeakBtn !== btn) finishSpeakBtn(activeSpeakBtn);
  activeSpeakBtn = btn || null;
  if (btn) btn._onEnd = onEnd || null;
  setSpeakBtn(btn, true);
  speak(text, () => {
    if (btn) finishSpeakBtn(btn);
    else if (onEnd) onEnd();
  });
}

function finishSpeakBtn(btn) {
  setSpeakBtn(btn, false);
  if (activeSpeakBtn === btn) activeSpeakBtn = null;
  const cb = btn._onEnd;
  btn._onEnd = null;
  if (cb) cb();
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (_) {}
  ta.remove(); return ok;
}

function addMsgActions(bubble, text) {
  if (!bubble || !text || !text.trim()) return null;
  bubble.querySelector(".msg-actions")?.remove();
  const bar = document.createElement("div");
  bar.className = "msg-actions";
  const copyBtn = document.createElement("button");
  copyBtn.className = "msg-act"; copyBtn.title = "复制"; copyBtn.innerHTML = ICON_COPY;
  const speakBtn = document.createElement("button");
  speakBtn.className = "msg-act"; speakBtn.title = "朗读"; speakBtn.innerHTML = ICON_SPEAK;

  copyBtn.onclick = async (e) => {
    e.stopPropagation();
    // copy what you see (rendered text, no markdown symbols)
    const clone = bubble.cloneNode(true);
    clone.querySelector(".msg-actions")?.remove();
    const plain = (clone.innerText || text).trim();
    const ok = await copyText(plain);
    copyBtn.innerHTML = ok ? ICON_OK : ICON_COPY;
    copyBtn.classList.toggle("done", ok);
    copyBtn.title = ok ? "已复制" : "复制失败";
    setTimeout(() => { copyBtn.innerHTML = ICON_COPY; copyBtn.classList.remove("done"); copyBtn.title = "复制"; }, 1500);
  };
  speakBtn.onclick = (e) => {
    e.stopPropagation();
    if (speakBtn.classList.contains("on")) {
      stopSpeaking();
      finishSpeakBtn(speakBtn);
    } else {
      speakWithButton(text, speakBtn);
    }
  };
  bar.appendChild(copyBtn);
  bar.appendChild(speakBtn);
  bubble.appendChild(bar);
  return speakBtn;
}

function handleWsEvent(d) {
  if (d.type === "route") {
    activeAiText = "";
    activeAiRow = document.createElement("div");
    activeAiRow.className = "msg-row ai";

    activeAiBubble = document.createElement("div");
    activeAiBubble.className = "msg-bubble";

    const m = document.createElement("div");
    m.className = "msg-meta";
    m.textContent = `${d.route === "agy" ? "🚀 Gemini Ultra" : (d.route === "tutor" ? "🎓 专属外教导师" : "💻 本地大脑")}`;

    activeAiRow.appendChild(activeAiBubble);
    activeAiRow.appendChild(m);
    $("#messages").appendChild(activeAiRow);
    $("#chatBox").scrollTop = $("#chatBox").scrollHeight;
  } else if (d.type === "status" || d.type === "tool") {
    if (!activeStepPill && activeAiRow) {
      activeStepPill = document.createElement("div");
      activeStepPill.className = "step-pill";
      activeAiRow.insertBefore(activeStepPill, activeAiBubble);
    }
    if (activeStepPill) {
      activeStepPill.textContent = `⚡ ${d.text}`;
    }
    $("#chatBox").scrollTop = $("#chatBox").scrollHeight;
  } else if (d.type === "token") {
    activeAiText += d.text;
    if (activeAiBubble) {
      activeAiBubble.innerHTML = renderMarkdown(activeAiText);
      $("#chatBox").scrollTop = $("#chatBox").scrollHeight;
    }
  } else if (d.type === "done") {
    let speakBtn = null;
    if (activeAiBubble) {
      activeAiBubble.innerHTML = renderMarkdown(d.text);
      speakBtn = addMsgActions(activeAiBubble, d.text);
    } else {
      const r = appendMsg("ai", d.text, d.route);
      speakBtn = r.bubble.querySelector(".msg-act[title='朗读']");
    }
    if (activeStepPill) {
      activeStepPill.remove();
      activeStepPill = null;
    }
    activeAiRow = null;
    activeAiBubble = null;
    activeAiText = "";

    if (isCallMode) {
      $("#callStatusText").textContent = "连麦中 · 🗣️ 导师正在回复讲解...";
      speakWithButton(d.text, speakBtn, () => {
        isAiResponding = false;
        if (isCallMode) {
          $("#callStatusText").textContent = "连麦中 · 👂 正在倾听你说话...";
        }
      });
    } else if ($("#ttsToggle").checked && d.text) {
      speakWithButton(d.text, speakBtn);
    }
  }
}

function sendText(text, mode = "auto") {
  const clean = (text || $("#textInput").value).trim();
  if (!clean) return;

  appendMsg("user", clean);
  $("#textInput").value = "";
  adjustTextareaHeight();

  const payload = {
    text: clean,
    mode: isCallMode ? "tutor" : mode,
    session: currentSid
  };
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

$("#sendBtn").onclick = () => sendText();
$("#textInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendText();
  }
});

function adjustTextareaHeight() {
  const ta = $("#textInput");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 80) + "px";
}
$("#textInput").addEventListener("input", adjustTextareaHeight);

// ---------------------------------------------------------------- Push-to-Talk Mic
$("#micBtn").onclick = async () => {
  if (isCallMode) {
    stopCallMode();
    return;
  }
  if (isVoiceRecording) {
    stopVoiceRecord();
  } else {
    startVoiceRecord();
  }
};

async function startVoiceRecord() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRec = new MediaRecorder(stream);
    audioChunks = [];
    mediaRec.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      $("#micBtn").classList.remove("recording");
      $("#micBtn").innerHTML = `<span class="mic-icon">⏳</span>`;
      const fd = new FormData();
      fd.append("audio", new Blob(audioChunks, { type: mediaRec.mimeType || "audio/webm" }), "rec.webm");
      try {
        const r = await fetch("/api/voice/stt", { method: "POST", body: fd });
        const j = await r.json();
        if (j.text) {
          sendText(j.text);
        }
      } catch (err) {
        alert("语音识别失败：" + err);
      } finally {
        $("#micBtn").innerHTML = `<span class="mic-icon">🎤</span>`;
      }
    };
    mediaRec.start();
    isVoiceRecording = true;
    $("#micBtn").classList.add("recording");
    $("#micBtn").innerHTML = `<span class="mic-icon">⏹</span>`;
  } catch (err) {
    alert("无法获取麦克风权限：" + err);
  }
}

function stopVoiceRecord() {
  if (mediaRec && mediaRec.state === "recording") {
    mediaRec.stop();
  }
  isVoiceRecording = false;
}

// ---------------------------------------------------------------- Hands-Free Call Mode (连麦通话)
$("#callModeBtn").onclick = () => {
  if (isCallMode) {
    stopCallMode();
  } else {
    startCallMode();
  }
};

$("#hangupBtn").onclick = () => stopCallMode();

async function startCallMode() {
  try {
    vadStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    alert("开启连麦通话失败，无法访问麦克风：" + err);
    return;
  }

  isCallMode = true;
  isAiResponding = false;
  vadSpeaking = false;
  $("#callModeBtn").classList.add("active");
  $("#callModeText").textContent = "📞 连麦通话中 (免按键)";
  $("#callBanner").classList.remove("hidden");
  $("#callStatusText").textContent = "连麦中 · 👂 正在倾听你说话...";
  appendMsg("ai", "✨ 已接通实时语音连麦！你可以直接开始说话，不用按任何按键。有说不上来的词、卡壳或读错的地方，我会用中文详细解释并示范正确的说法！", "连麦外教已接入");

  vadAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = vadAudioCtx.createMediaStreamSource(vadStream);
  vadAnalyser = vadAudioCtx.createAnalyser();
  vadAnalyser.fftSize = 512;
  source.connect(vadAnalyser);

  const bufferLength = vadAnalyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);

  function createCallRecorder() {
    callChunks = [];
    callMediaRec = new MediaRecorder(vadStream);
    callMediaRec.ondataavailable = (e) => {
      if (e.data.size > 0) callChunks.push(e.data);
    };
    callMediaRec.onstop = async () => {
      if (!isCallMode || callChunks.length === 0) return;
      isAiResponding = true;
      $("#callStatusText").textContent = "连麦中 · 🤔 正在分析理解与思考...";

      const audioBlob = new Blob(callChunks, { type: callMediaRec.mimeType || "audio/webm" });
      const fd = new FormData();
      fd.append("audio", audioBlob, "call_voice.webm");

      try {
        const r = await fetch("/api/voice/stt", { method: "POST", body: fd });
        const j = await r.json();
        if (j.text && j.text.trim()) {
          sendText(j.text, "tutor");
        } else {
          isAiResponding = false;
          if (isCallMode) $("#callStatusText").textContent = "连麦中 · 👂 正在倾听你说话...";
        }
      } catch (err) {
        isAiResponding = false;
        if (isCallMode) $("#callStatusText").textContent = "连麦中 · 👂 正在倾听你说话...";
      }
    };
  }

  function loop() {
    if (!isCallMode) return;
    vadAnalyser.getByteFrequencyData(dataArray);

    let sum = 0;
    for (let i = 0; i < bufferLength; i++) {
      sum += dataArray[i];
    }
    const avg = sum / bufferLength;

    const threshold = 18; // speaking volume threshold
    const now = Date.now();

    if (!isAiResponding) {
      if (avg > threshold) {
        if (!vadSpeaking) {
          vadSpeaking = true;
          createCallRecorder();
          callMediaRec.start();
          $("#callStatusText").textContent = "连麦中 · 🎙️ 正在说话...";
        }
        vadSilenceStart = now;
      } else if (vadSpeaking) {
        // user was speaking, check silence duration
        if (now - vadSilenceStart > 1300) {
          // silence > 1.3s -> user finished sentence
          vadSpeaking = false;
          if (callMediaRec && callMediaRec.state === "recording") {
            callMediaRec.stop();
          }
        }
      }
    }

    vadAnimId = requestAnimationFrame(loop);
  }

  loop();
}

function stopCallMode() {
  isCallMode = false;
  isAiResponding = false;
  vadSpeaking = false;
  if (vadAnimId) cancelAnimationFrame(vadAnimId);
  if (vadStream) {
    vadStream.getTracks().forEach((t) => t.stop());
    vadStream = null;
  }
  if (vadAudioCtx) {
    vadAudioCtx.close();
    vadAudioCtx = null;
  }
  $("#callModeBtn").classList.remove("active");
  $("#callModeText").textContent = "开启连麦通话 (免按键持续交流)";
  $("#callBanner").classList.add("hidden");
  appendMsg("ai", "📞 已挂断连麦通话。需要练习随时点上方再次开启！");
}

// ---------------------------------------------------------------- Audio Output & Speech
function browserSpeak(text, onEnd) {
  if (!window.speechSynthesis) {
    if (onEnd) onEnd();
    return;
  }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = /[\u4e00-\u9fff]/.test(text) ? "zh-CN" : "en-US";
  u.onend = () => { if (onEnd) onEnd(); };
  u.onerror = () => { if (onEnd) onEnd(); };
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

function sanitizeForSpeech(text) {
  if (!text) return "";
  let clean = text
    .replace(/```[\s\S]*?```/g, "")
    // Strip all emojis and flags so TTS never speaks "灯泡", "旗帜", etc.
    .replace(/[\u{1F300}-\u{1F9FF}\u{1F600}-\u{1F64F}\u{1F680}-\u{1F6FF}\u{2600}-\u{27BF}\u{1FA70}-\u{1FAFF}\u{1F1E6}-\u{1F1FF}\u{2300}-\u{23FF}\u{2B50}\u{FE0F}\u{200D}]/gu, "")
    // Strip horizontal rules, headers, bullets, blockquote markers
    .replace(/^[-=_]{2,}\s*$/gm, "")
    .replace(/^[#>*\-\d\.]+\s*/gm, "")
    .replace(/[*_`>~]/g, "")
    // Clean dashes and colons
    .replace(/[-—]{2,}/g, "，")
    .replace(/[：:]{2,}/g, "：")
    .trim();
  return clean.replace(/\n+/g, "，").slice(0, 1500);
}

let speakToken = 0;
let currentAudio = null;

function stopSpeaking() {
  speakToken++;
  if (currentAudio) { try { currentAudio.pause(); } catch (_) {} currentAudio = null; }
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (_) {}
}

// Split a reply into speakable chunks at line/section boundaries; the first chunk is short so audio starts fast.
function chunkForSpeech(text) {
  const lines = String(text).replace(/```[\s\S]*?```/g, "").split("\n");
  const chunks = [];
  let cur = "";
  for (const ln of lines) {
    const limit = chunks.length === 0 ? 110 : 220;
    const isHeader = /^\s*#{1,6}\s/.test(ln);
    if (cur.trim() && (isHeader || cur.length + ln.length > limit)) { chunks.push(cur); cur = ""; }
    cur += ln + "\n";
  }
  if (cur.trim()) chunks.push(cur);
  return chunks.filter((c) => sanitizeForSpeech(c).trim());
}

async function speak(text, onEnd) {
  stopSpeaking();
  const my = speakToken;
  const chunks = chunkForSpeech(text || "");
  if (!chunks.length) { if (onEnd) onEnd(); return; }
  const context = String(text).slice(0, 6000);
  const jobs = [];
  const get = (i) => jobs[i] || (jobs[i] = fetch("/api/voice/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: chunks[i], context })
  }).then((r) => (r.ok ? r.blob() : null)).catch(() => null));

  get(0);
  if (chunks.length > 1) get(1);
  for (let i = 0; i < chunks.length; i++) {
    const blob = await get(i);
    if (my !== speakToken) return;
    if (i + 1 < chunks.length) get(i + 1);
    if (i + 2 < chunks.length) get(i + 2);
    if (!blob) {
      if (i === 0) { browserSpeak(sanitizeForSpeech(text), onEnd); return; }
      continue;
    }
    await new Promise((res) => {
      const a = new Audio(URL.createObjectURL(blob));
      currentAudio = a;
      a.onended = res;
      a.onerror = res;
      a.play().catch(res);
    });
    if (my !== speakToken) return;
  }
  currentAudio = null;
  if (onEnd) onEnd();
}

// ---------------------------------------------------------------- Workflow / Recipe Recording
$("#recordBtn").onclick = () => {
  if (!isRecordingWf) {
    startWfRecording();
  } else {
    stopWfRecording();
  }
};

$("#stopRecordBannerBtn").onclick = () => stopWfRecording();

function startWfRecording() {
  isRecordingWf = true;
  $("#recordBtn").classList.add("recording");
  $("#recordBtnText").textContent = "⏹ 保存流程";
  $("#recordBanner").classList.remove("hidden");
  appendMsg("ai", "✨ 已开启工作流程录制！接下来请演示或说明你要做的步骤，我会全程学习记录。完成时点击「保存流程」即可。");
}

function stopWfRecording() {
  isRecordingWf = false;
  $("#recordBtn").classList.remove("recording");
  $("#recordBtnText").textContent = "⏺ 记录流程";
  $("#recordBanner").classList.add("hidden");
  $("#saveModal").classList.remove("hidden");
  $("#recipeNameInput").value = "";
  $("#recipeNameInput").focus();
}

$("#closeSaveBtn").onclick = () => $("#saveModal").classList.add("hidden");
$("#cancelSaveBtn").onclick = () => $("#saveModal").classList.add("hidden");

$("#confirmSaveBtn").onclick = async () => {
  const name = $("#recipeNameInput").value.trim();
  if (!name) {
    alert("请输入流程名称！");
    return;
  }
  $("#confirmSaveBtn").textContent = "正在提炼中...";
  $("#confirmSaveBtn").disabled = true;

  try {
    const res = await fetch("/api/recipes/distill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: currentSid, name })
    });
    const data = await res.json();
    $("#saveModal").classList.add("hidden");
    appendMsg("ai", `🎉 流程「${name}」已成功学习并保存入库！\n\n📌 步骤清单：\n` + (data.steps || []).map((s, i) => `${i + 1}. ${s}`).join("\n") + "\n\n以后你可以点击「📂 载入流程」随时唤醒我为你执行！");
  } catch (err) {
    alert("提炼保存失败：" + err);
  } finally {
    $("#confirmSaveBtn").textContent = "提炼并保存";
    $("#confirmSaveBtn").disabled = false;
  }
};

// ---------------------------------------------------------------- Recipes List & Run
$("#recipesBtn").onclick = async () => {
  $("#recipesModal").classList.remove("hidden");
  loadRecipes();
};
$("#closeRecipesBtn").onclick = () => $("#recipesModal").classList.add("hidden");

async function loadRecipes() {
  const list = $("#recipesList");
  list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text-dim)">加载中...</div>`;
  try {
    const r = await fetch("/api/recipes");
    const recipes = await r.json();
    if (!recipes || !recipes.length) {
      list.innerHTML = `<div class="empty-tip">暂无保存的流程，点击「⏺ 记录流程」开始录制教学！</div>`;
      return;
    }
    list.innerHTML = "";
    recipes.forEach((item) => {
      const card = document.createElement("div");
      card.className = "recipe-card";
      card.innerHTML = `
        <div class="recipe-title-row">
          <span class="recipe-name">${escapeHtml(item.name)}</span>
          <span style="font-size:10px;color:var(--text-dim)">${(item.updated_at || "").slice(0, 10)}</span>
        </div>
        <div class="recipe-desc">${escapeHtml(item.description || "工作流操作指南")}</div>
        <div class="recipe-steps-preview">
          ${(item.steps || []).slice(0, 3).map((s, i) => `<div>${i + 1}. ${escapeHtml(s)}</div>`).join("")}
          ${(item.steps && item.steps.length > 3) ? `<div style="color:var(--accent-light)">...共 ${item.steps.length} 个步骤</div>` : ""}
        </div>
        <div class="recipe-card-actions">
          <button class="btn-del" data-del="${escapeHtml(item.name)}">删除</button>
          <button class="btn-run" data-run="${escapeHtml(item.name)}">▶ 加载执行</button>
        </div>
      `;
      list.appendChild(card);
    });

    list.querySelectorAll("[data-run]").forEach((btn) => {
      btn.onclick = async () => {
        const name = btn.dataset.run;
        $("#recipesModal").classList.add("hidden");
        const r = await fetch(`/api/recipes/${encodeURIComponent(name)}`);
        const item = await r.json();
        const prompt = item.prompt_template || `请按照我们之前录制的工作流程「${item.name}」，开始执行操作。`;
        sendText(prompt);
      };
    });

    list.querySelectorAll("[data-del]").forEach((btn) => {
      btn.onclick = async () => {
        const name = btn.dataset.del;
        if (!confirm(`确定删除工作流程「${name}」吗？`)) return;
        await fetch(`/api/recipes/${encodeURIComponent(name)}`, { method: "DELETE" });
        loadRecipes();
      };
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-tip" style="color:#f87171">加载失败: ${err}</div>`;
  }
}

// ---------------------------------------------------------------- Avatar Switcher
$("#avatarBtn").onclick = openAvatarModal;
$("#changeAvatarBtn").onclick = openAvatarModal;
$("#closeAvatarBtn").onclick = () => $("#avatarModal").classList.add("hidden");

async function openAvatarModal() {
  $("#avatarModal").classList.remove("hidden");
  const grid = $("#avatarGrid");
  grid.innerHTML = `<div style="grid-column:1/3;text-align:center;padding:10px;color:var(--text-dim)">加载中...</div>`;
  try {
    const res = await fetch("/api/avatar/styles");
    const styles = await res.json();
    grid.innerHTML = "";
    styles.forEach((st) => {
      const el = document.createElement("div");
      el.className = "avatar-item";
      el.innerHTML = `
        <img class="avatar-thumb" src="${st.url}" alt="${st.name}" />
        <span class="avatar-label">${st.name}</span>
      `;
      el.onclick = async () => {
        await fetch("/api/avatar/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ style: st.id })
        });
        $("#avatarImg").src = `/static/avatar.png?t=${Date.now()}`;
        $("#avatarModal").classList.add("hidden");
        appendMsg("ai", `✨ 形象已切换为「${st.name}」！桌面图标也已同步更新。`);
      };
      grid.appendChild(el);
    });
  } catch (err) {
    grid.innerHTML = `<div style="grid-column:1/3;color:#f87171">加载失败: ${err}</div>`;
  }
}

// ---------------------------------------------------------------- Open Full Dashboard
$("#openFullBtn").onclick = () => {
  window.open("http://127.0.0.1:9600/", "_blank");
};

// ---------------------------------------------------------------- Polling Status
async function pollStatus() {
  try {
    const r = await fetch("/api/status");
    const st = await r.json();
    if (st.gpu && st.gpu.gpus && st.gpu.gpus.length) {
      const g = st.gpu.gpus[0];
      $("#vramPill").textContent = `显存: ${Math.round(g.mem_used_mb)} / ${Math.round(g.mem_total_mb)} MB`;
    }
  } catch (_) {}
}
setInterval(pollStatus, 5000);
pollStatus();

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------- Init
async function initSession() {
  try {
    const r = await fetch("/api/sessions", { method: "POST" });
    const s = await r.json();
    currentSid = s.id;
  } catch (_) {}
  connectWs();
}
initSession();

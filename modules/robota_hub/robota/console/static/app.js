// Robota console front-end (vanilla JS)
const $ = (s) => document.querySelector(s);
const esc = (s) => (s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const ROUTE_LABEL = { local: "💬 本地聊天", local_agent: "🛠 本地代理", agy: "🚀 agy", provider: "🌍 外部服务", tutor: "🎓 导师" };

let session = localStorage.getItem("robota.session") || null;
let chatWS = null, busy = false, current = null;

function md(text) {
  let h = esc(text);
  h = h.replace(/```(\w*)\n([\s\S]*?)```/g, (_, l, c) => `<pre><code>${c}</code></pre>`);
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  h = h.replace(/\n/g, "<br>");
  h = h.replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/g, (m, c) => `<pre><code>${c.replace(/<br>/g, "\n")}</code></pre>`);
  return h;
}

function addMsg(role, text, meta = "") {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = `<div class="who">${role === "user" ? "🧑" : "🤖"}</div><div class="body"><div class="tools"></div><div class="content">${md(text)}</div><div class="meta">${esc(meta)}</div></div>`;
  if (!text) el.querySelector(".tools").style.display = "none";
  $("#messages").appendChild(el);
  $("#messages").scrollTop = 1e9;
  return el;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

// ---------------------------------------------------------------- sessions
async function loadSessions() {
  const list = await api("/api/sessions");
  const ul = $("#sessions");
  ul.innerHTML = "";
  list.forEach((s) => {
    const li = document.createElement("li");
    if (s.id === session) li.className = "active";
    li.innerHTML = `<span class="t" title="${esc(s.title)}">${s.mode === "tutor" ? "🎓 " : ""}${esc(s.title)}</span><span class="x" title="删除">✕</span>`;
    li.onclick = () => openSession(s.id);
    li.querySelector(".x").onclick = async (e) => {
      e.stopPropagation();
      if (!confirm("删除这个对话？")) return;
      await api(`/api/sessions/${s.id}`, { method: "DELETE" });
      if (s.id === session) { session = null; localStorage.removeItem("robota.session"); $("#messages").innerHTML = ""; }
      loadSessions();
    };
    ul.appendChild(li);
  });
}

async function openSession(id) {
  session = id;
  localStorage.setItem("robota.session", id);
  const s = await api(`/api/sessions/${id}`);
  $("#messages").innerHTML = "";
  if (s.mode) $("#mode").value = s.mode;
  $("#project").value = s.project || "";
  s.messages.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.content,
    m.role === "user" ? m.time || "" : `${ROUTE_LABEL[m.route] || m.route || ""} · ${m.model || ""} · ${m.time || ""}`));
  loadSessions();
}

$("#newChat").onclick = async () => {
  const s = await api("/api/sessions", { method: "POST" });
  await openSession(s.id);
  $("#input").focus();
};

// ---------------------------------------------------------------- chat
function connectChat() {
  chatWS = new WebSocket(`ws://${location.host}/ws/chat`);
  chatWS.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.session && ev.session !== session) { session = ev.session; localStorage.setItem("robota.session", session); }
    if (!current) return;
    const body = current.querySelector(".content");
    const tools = current.querySelector(".tools");
    if (ev.type === "route") { $("#route").textContent = ROUTE_LABEL[ev.route] || ev.route; }
    else if (ev.type === "text") { current._buf = (current._buf || "") + ev.text; body.innerHTML = md(current._buf); body.classList.add("typing"); }
    else if (ev.type === "tool" || ev.type === "status") { tools.style.display = "block"; tools.textContent += (ev.type === "tool" ? "🔧 " : "ℹ ") + ev.text + "\n"; }
    else if (ev.type === "done") {
      body.classList.remove("typing");
      body.innerHTML = md(ev.text || current._buf || "");
      const u = ev.usage || {};
      current.querySelector(".meta").textContent = `${ROUTE_LABEL[ev.route] || ev.route} · ${ev.model}${u.total_tokens ? " · " + u.total_tokens + " tokens" : ""}`;
      if ($("#speak").checked && ev.ok) speak(ev.text);
      current = null; busy = false; $("#send").disabled = false;
      loadSessions();
    }
    $("#messages").scrollTop = 1e9;
  };
  chatWS.onclose = () => setTimeout(connectChat, 1500);
}

function send() {
  const text = $("#input").value.trim();
  if (!text || busy) return;
  if (!chatWS || chatWS.readyState !== 1) { alert("还没连上 Robota，稍等一下再试"); return; }
  addMsg("user", text, new Date().toTimeString().slice(0, 5));
  current = addMsg("bot", "");
  current.querySelector(".content").classList.add("typing");
  busy = true; $("#send").disabled = true;
  chatWS.send(JSON.stringify({ session, text, mode: $("#mode").value, project: $("#project").value, model: $("#model").value.trim() }));
  $("#input").value = "";
}
$("#send").onclick = send;
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$("#popoutMini").onclick = () => {
  window.open("/mini", "robota_mini", "width=380,height=680,menubar=no,toolbar=no,location=no,status=no,resizable=yes");
};

// ---------------------------------------------------------------- voice
let rec = null, chunks = [];
$("#mic").onclick = async () => {
  if (rec && rec.state === "recording") { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(stream);
    chunks = [];
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      $("#mic").classList.remove("rec"); $("#mic").textContent = "⏳";
      const fd = new FormData();
      fd.append("audio", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "rec.webm");
      try {
        const r = await fetch("/api/voice/stt", { method: "POST", body: fd });
        const j = await r.json();
        if (j.text) { $("#input").value = j.text; send(); } else alert("没听清：" + JSON.stringify(j).slice(0, 200));
      } catch (err) { alert("语音识别失败：" + err); }
      $("#mic").textContent = "🎤";
    };
    rec.start();
    $("#mic").classList.add("rec"); $("#mic").textContent = "⏺";
  } catch (err) { alert("无法使用麦克风：" + err); }
};

function browserSpeak(text, lang) {
  if (!window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang || (/[\u4e00-\u9fff]/.test(text) ? "zh-CN" : "en-US");
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}
async function speak(text) {
  const clean = text.replace(/```[\s\S]*?```/g, "").replace(/[`*#>]/g, "").slice(0, 1200);
  if (!clean.trim()) return;
  const lang = $("#mode").value === "tutor" ? ($("#tutorLang").value || "") : "";
  try {
    const r = await fetch("/api/voice/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: text.slice(0, 3800), lang }) });
    if (!r.ok) { browserSpeak(clean); return; }
    new Audio(URL.createObjectURL(await r.blob())).play();
  } catch (_) { browserSpeak(clean); }
}

// ---------------------------------------------------------------- status panel
function renderStatus(st) {
  const q = st.queue;
  $("#qstate").textContent = q.halted ? "已暂停" : (q.running ? "运行中" : "空闲");
  $("#qRunning").textContent = q.running ? `▶ ${q.running.title}（${q.running.started}）` : "";
  const ul = $("#tasks"); ul.innerHTML = "";
  q.recent.forEach((t) => {
    const li = document.createElement("li");
    li.innerHTML = `<div class="h"><b>${esc(t.title)}</b><span class="s-${t.state}">${t.state}</span></div>
      <div class="muted">${t.kind}${t.project ? " · " + t.project : ""}${t.route ? " · " + t.route : ""} · ${t.created.slice(5, 16)}</div>
      <div class="acts"><button class="btn sm" data-a="log">日志</button>${t.state === "queued" ? '<button class="btn sm" data-a="cancel">取消</button>' : ""}${["failed", "stopped", "quarantined"].includes(t.state) ? '<button class="btn sm" data-a="retry">重试</button>' : ""}</div>`;
    li.querySelectorAll("button").forEach((b) => b.onclick = async () => {
      if (b.dataset.a === "log") { showModal(await api(`/api/queue/${t.id}/log`)); return; }
      await api(`/api/queue/${t.id}/${b.dataset.a}`, { method: "POST" }); refresh();
    });
    ul.appendChild(li);
  });
  const g = st.gpu || {};
  const gg = (g.gpus || [])[0] || {};
  const loaded = ((g.ollama || {}).models || []).map((m) => m.name || m.model).join(", ") || "无";
  $("#gpu").textContent = gg.name ? `${gg.name}\n已用 ${gg.memory_used_mb ?? gg.mem_used_mb ?? "?"} / ${gg.memory_total_mb ?? gg.mem_total_mb ?? "?"} MB\nOllama 已加载：${loaded}` : JSON.stringify(g).slice(0, 300);
  const u = st.usage;
  $("#usage").textContent = `对话 ${u.messages} 条（本地 ${u.local_msgs} · agy ${u.agy_msgs}）\nagy tokens：${u.agy_tokens.toLocaleString()}`;
  $("#services").textContent = `agy：${st.agy ? "✅" : "❌"}   语音：${st.voice ? "✅ 运行中" : "⏸ 按需启动"}\n外部服务：${st.providers.length ? st.providers.join(", ") : "未配置（secrets\\ 放 key 启用）"}`;
  if (!$("#tutorLang").value && st.tutor) $("#tutorLang").value = st.tutor.target_language || "";
}

function fillProjects(list) {
  for (const sel of [$("#project"), $("#tProject")]) {
    if (sel.options.length > 1) continue;
    list.forEach((p) => { const o = document.createElement("option"); o.value = p.id; o.textContent = (p.private ? "🔒 " : "") + p.name; sel.appendChild(o); });
  }
}

async function refresh() {
  try { const st = await api("/api/status"); fillProjects(st.projects); renderStatus(st); } catch (_) {}
}

function connectStatus() {
  const ws = new WebSocket(`ws://${location.host}/ws/status`);
  ws.onmessage = () => refresh();
  ws.onclose = () => setTimeout(connectStatus, 2000);
}

function showModal(text) { $("#modalBody").textContent = text; $("#modal").classList.remove("hidden"); }
$("#modalClose").onclick = () => $("#modal").classList.add("hidden");

$("#qHalt").onclick = async () => { await api("/api/queue/halt", { method: "POST" }); refresh(); };
$("#qResume").onclick = async () => { await api("/api/queue/resume", { method: "POST" }); refresh(); };
$("#qKill").onclick = async () => { if (confirm("中止正在运行的任务？")) { await api("/api/queue/kill", { method: "POST" }); refresh(); } };
$("#gpuFree").onclick = async () => { const r = await api("/api/gpu/free", { method: "POST" }); showModal(JSON.stringify(r, null, 2)); refresh(); };
$("#tAdd").onclick = async () => {
  const kind = $("#tKind").value, body = $("#tBody").value.trim();
  const payload = { title: $("#tTitle").value.trim(), kind, project: $("#tProject").value || null, brain: $("#tBrain").value, gpu: $("#tGpu").checked };
  if (kind === "command") payload.command = body; else payload.prompt = body;
  const r = await api("/api/queue", { method: "POST", body: JSON.stringify(payload) });
  if (!r.ok) { alert(r.error || JSON.stringify(r)); return; }
  $("#tTitle").value = ""; $("#tBody").value = ""; refresh();
};
$("#tutorSet").onclick = async () => { await api("/api/tutor/language", { method: "POST", body: JSON.stringify({ target_language: $("#tutorLang").value }) }); alert("已保存"); };
$("#tutorSum").onclick = async () => {
  if (!session) return alert("先进行一次陪练对话");
  const r = await api(`/api/tutor/summarize/${session}`, { method: "POST" });
  showModal(r.ok ? "已记入学习档案：\n\n" + r.text : "失败：" + r.error);
};

// ---------------------------------------------------------------- boot
connectChat(); connectStatus(); refresh(); setInterval(refresh, 5000);
loadSessions().then(() => { if (session) openSession(session).catch(() => { session = null; }); });

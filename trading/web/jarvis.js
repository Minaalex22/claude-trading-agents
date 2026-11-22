// JARVIS: hologram avatar (three.js), voice in (Web Speech recognition) and voice out (speech synthesis).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { talk: 0, listening: false, speaking: false, unlocked: false, enabled: false };

  // ---------- Hologram: Iron Man style HUD (crisp 2D canvas, no particles) ----------
  // A glowing core, segmented rotating rings, a tick scale, a wireframe globe, a voice waveform ring and live
  // desk readouts. Cyan is the hologram, orange (#FF5C00) marks the app's accents and alerts.
  const CYAN = "63,224,255", ORANGE = "255,92,0", RED = "255,70,70";
  const hud = { mode: "locked", thinking: 0, data: { pnl: null, win: null, trades: null, agents: null } };

  function fmtMoney(v) { if (v == null) return "--"; const s = v < 0 ? "-" : "+"; return s + "$" + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 }); }
  async function loadHudData() {
    try {
      const [t, s] = await Promise.all([
        fetch("/api/trades", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch("/api/state", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
      if (t && t.totals) { hud.data.pnl = t.totals.pnl; hud.data.win = t.totals.win_rate; hud.data.trades = t.totals.trades; }
      if (s && s.agents_reporting != null) hud.data.agents = s.agents_reporting;
    } catch (e) { /* readouts stay "--" */ }
  }

  function startHologram() {
    const host = $("holo");
    const cv = document.createElement("canvas");
    host.appendChild(cv);
    const ctx = cv.getContext("2d");
    if (!ctx) { $("state").textContent = "hologram unavailable"; return; }
    let W = 0, H = 0, dpr = 1, mx = 0, my = 0;
    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = host.clientWidth; H = host.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    window.addEventListener("resize", resize); resize();
    host.addEventListener("pointermove", (e) => {
      const r = host.getBoundingClientRect();
      mx = (e.clientX - r.left) / r.width - 0.5; my = (e.clientY - r.top) / r.height - 0.5;
    });

    const wave = new Array(96).fill(0);
    const glow = (rgb, blur) => { ctx.shadowColor = "rgba(" + rgb + ",0.9)"; ctx.shadowBlur = blur; };
    const stroke = (rgb, a, w) => { ctx.strokeStyle = "rgba(" + rgb + "," + a + ")"; ctx.lineWidth = w; };

    function arc(cx, cy, r, a0, a1, rgb, a, w) { ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1); stroke(rgb, a, w); ctx.stroke(); }
    function segments(cx, cy, r, n, fill, rot, rgb, a, w) {
      const step = (Math.PI * 2) / n;
      for (let i = 0; i < n; i++) { const s = rot + i * step; arc(cx, cy, r, s, s + step * fill, rgb, a, w); }
    }
    function ticks(cx, cy, r, n, len, rot, rgb, a) {
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const t = rot + (i / n) * Math.PI * 2, L = i % 5 === 0 ? len * 1.9 : len;
        ctx.moveTo(cx + Math.cos(t) * r, cy + Math.sin(t) * r);
        ctx.lineTo(cx + Math.cos(t) * (r - L), cy + Math.sin(t) * (r - L));
      }
      stroke(rgb, a, 1); ctx.stroke();
    }
    function globe(cx, cy, r, rotY, tilt, rgb, a) { // wireframe sphere: latitude + longitude lines, back lines dimmer
      const ct = Math.cos(tilt), st = Math.sin(tilt);
      const proj = (x, y, z) => { const y2 = y * ct - z * st, z2 = y * st + z * ct; return [cx + x * r, cy + y2 * r, z2]; };
      const line = (pts) => {
        for (let i = 1; i < pts.length; i++) {
          const p = pts[i - 1], q = pts[i], front = (p[2] + q[2]) > 0;
          ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
          stroke(rgb, front ? a : a * 0.25, front ? 1 : 0.6); ctx.stroke();
        }
      };
      for (let k = 1; k < 6; k++) { // latitudes
        const phi = -Math.PI / 2 + (k * Math.PI) / 6, y = Math.sin(phi), rr = Math.cos(phi), pts = [];
        for (let i = 0; i <= 48; i++) { const th = (i / 48) * Math.PI * 2 + rotY; pts.push(proj(rr * Math.cos(th), y, rr * Math.sin(th))); }
        line(pts);
      }
      for (let k = 0; k < 8; k++) { // longitudes
        const th = (k / 8) * Math.PI + rotY, pts = [];
        for (let i = 0; i <= 40; i++) { const phi = -Math.PI / 2 + (i / 40) * Math.PI; pts.push(proj(Math.cos(phi) * Math.cos(th), Math.sin(phi), Math.cos(phi) * Math.sin(th))); }
        line(pts);
      }
    }
    function bracket(x, y, w, h, rgb, a) { // HUD corner brackets
      const c = 14; ctx.beginPath();
      ctx.moveTo(x, y + c); ctx.lineTo(x, y); ctx.lineTo(x + c, y);
      ctx.moveTo(x + w - c, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + c);
      ctx.moveTo(x + w, y + h - c); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - c, y + h);
      ctx.moveTo(x + c, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - c);
      stroke(rgb, a, 1.2); ctx.stroke();
    }
    function readout(x, y, label, value, rgb, align) {
      ctx.textAlign = align; ctx.shadowBlur = 0;
      ctx.font = "600 10px ui-monospace,SFMono-Regular,Menlo,monospace"; ctx.fillStyle = "rgba(" + CYAN + ",0.6)";
      ctx.fillText(label, x, y);
      glow(rgb, 10); ctx.font = "600 20px ui-monospace,SFMono-Regular,Menlo,monospace"; ctx.fillStyle = "rgba(" + rgb + ",0.95)";
      ctx.fillText(value, x, y + 22); ctx.shadowBlur = 0;
      const lx = align === "left" ? x : x - 120; ctx.beginPath(); ctx.moveTo(lx, y + 30); ctx.lineTo(lx + 120, y + 30);
      stroke(rgb, 0.35, 1); ctx.stroke();
    }

    let last = performance.now(), t = 0;
    (function frame(now) {
      const dt = Math.min(0.05, ((now || last) - last) / 1000); last = now || last; t += dt;
      if (state.speaking) state.talk = Math.max(state.talk, 0.35 + 0.45 * Math.abs(Math.sin(t * 13)));
      state.talk *= 0.9;
      const mode = !state.enabled ? "offline" : !state.unlocked ? "locked" : state.speaking ? "speaking"
        : hud.thinking > 0 ? "thinking" : state.listening ? "listening" : "ready";
      const live = mode !== "locked" && mode !== "offline";
      const main = live ? CYAN : ORANGE;
      const speed = mode === "thinking" ? 3 : mode === "listening" ? 1.8 : live ? 1 : 0.35;

      ctx.clearRect(0, 0, W, H);
      const cx = W / 2 + mx * 18, cy = H * 0.47 + my * 12, R = Math.min(W, H) * 0.36;

      // faint grid
      ctx.shadowBlur = 0; stroke(CYAN, 0.05, 1); ctx.beginPath();
      for (let x = (cx % 40); x < W; x += 40) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
      for (let y = (cy % 40); y < H; y += 40) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
      ctx.stroke();

      // core glow
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.42);
      g.addColorStop(0, "rgba(220,250,255," + (live ? 0.95 : 0.5) + ")");
      g.addColorStop(0.25, "rgba(" + main + "," + (0.55 + state.talk * 0.4) + ")");
      g.addColorStop(1, "rgba(" + main + ",0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 0.42, 0, Math.PI * 2); ctx.fill();

      glow(main, 12);
      // wireframe globe inside the core rings
      globe(cx, cy, R * 0.3, t * 0.5 * speed, 0.35 + my * 0.3, main, 0.55);
      arc(cx, cy, R * 0.34, 0, Math.PI * 2, main, 0.9, 2);
      segments(cx, cy, R * 0.4, 12, 0.7, -t * 0.8 * speed, main, 0.8, 3);
      // voice waveform ring
      for (let i = 0; i < wave.length; i++) {
        const target = live ? (state.talk * (0.4 + 0.6 * Math.abs(Math.sin(i * 0.7 + t * 9)))
          + (state.listening ? 0.12 + 0.1 * Math.abs(Math.sin(i * 1.3 + t * 5)) : 0.04)) : 0.02;
        wave[i] += (target - wave[i]) * 0.25;
      }
      ctx.beginPath();
      for (let i = 0; i < wave.length; i++) {
        const a = (i / wave.length) * Math.PI * 2, r0 = R * 0.46, r1 = r0 + 4 + wave[i] * R * 0.16;
        ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      }
      stroke(main, 0.85, 2); ctx.stroke();
      // tick scale and segmented outer rings
      ticks(cx, cy, R * 0.72, 120, 5, t * 0.15 * speed, main, 0.55);
      segments(cx, cy, R * 0.78, 3, 0.28, t * 0.4 * speed, ORANGE, 0.9, 3);
      segments(cx, cy, R * 0.84, 48, 0.55, -t * 0.25 * speed, main, 0.45, 2);
      arc(cx, cy, R * 0.92, 0, Math.PI * 2, main, 0.25, 1);
      segments(cx, cy, R * 0.97, 6, 0.12, t * 0.6 * speed, main, 0.8, 4);
      // scanning sweep
      const sw = t * 1.2 * speed;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R * 0.92, sw, sw + 0.35); ctx.closePath();
      const sg = ctx.createRadialGradient(cx, cy, R * 0.45, cx, cy, R * 0.92);
      sg.addColorStop(0, "rgba(" + main + ",0)"); sg.addColorStop(1, "rgba(" + main + ",0.18)");
      ctx.fillStyle = sg; ctx.shadowBlur = 0; ctx.fill();
      if (mode === "thinking") { glow(ORANGE, 14); arc(cx, cy, R * 0.6, t * 6, t * 6 + 1.2, ORANGE, 0.95, 3); arc(cx, cy, R * 0.6, t * 6 + Math.PI, t * 6 + Math.PI + 1.2, ORANGE, 0.95, 3); }

      // title + status
      ctx.shadowBlur = 0; ctx.textAlign = "center";
      glow(main, 14); ctx.fillStyle = "rgba(" + main + ",0.95)"; ctx.font = "700 " + Math.max(14, R * 0.09) + "px ui-monospace,SFMono-Regular,Menlo,monospace";
      ctx.fillText("J.A.R.V.I.S.", cx, cy + R * 1.12);
      ctx.shadowBlur = 0; ctx.font = "600 10px ui-monospace,SFMono-Regular,Menlo,monospace"; ctx.fillStyle = "rgba(" + CYAN + ",0.6)";
      ctx.fillText("ATS MATRIX  //  PRACTICE MODE  //  REAL MONEY LOCKED", cx, cy + R * 1.12 + 18);

      // readouts and brackets (only when there is room)
      const d = hud.data, wide = W > 620;
      bracket(10, 10, W - 20, H - 20, CYAN, 0.35);
      if (wide) {
        readout(28, 44, "DESK P&L", fmtMoney(d.pnl), d.pnl != null && d.pnl < 0 ? RED : main, "left");
        readout(28, 104, "WIN RATE", d.win == null ? "--" : (d.win * 100).toFixed(1) + "%", main, "left");
        readout(W - 28, 44, "CLOSED TRADES", d.trades == null ? "--" : d.trades.toLocaleString(), main, "right");
        readout(W - 28, 104, "AGENTS REPORTING", d.agents == null ? "--" : d.agents.toLocaleString(), main, "right");
        ctx.textAlign = "left"; ctx.font = "600 10px ui-monospace,SFMono-Regular,Menlo,monospace"; ctx.fillStyle = "rgba(" + CYAN + ",0.55)";
        ctx.fillText(new Date().toLocaleTimeString() + "  MELBOURNE", 28, H - 30);
        ctx.textAlign = "right"; ctx.fillStyle = "rgba(" + (live ? CYAN : ORANGE) + ",0.8)";
        ctx.fillText("STATUS: " + mode.toUpperCase(), W - 28, H - 30);
      }
      requestAnimationFrame(frame);
    })();
    loadHudData(); setInterval(loadHudData, 15000);
  }

  // ---------- Voice out ----------
  let voice = null;
  const PREFERRED = ["samantha", "victoria", "karen", "moira", "tessa", "serena", "female", "zira", "aria", "jenny",
                     "libby", "sonia", "natasha", "google uk english female", "google us english"];
  function pickVoice() {
    if (!("speechSynthesis" in window)) return;
    const voices = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
    for (const name of PREFERRED) { const v = voices.find((x) => x.name.toLowerCase().includes(name)); if (v) { voice = v; return; } }
    voice = voices[0] || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  function speak(text) {
    if (!$("voiceOn").checked || !("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.0; u.pitch = 1.08;
    u.onstart = () => { state.speaking = true; setState("speaking"); pauseListening(true); };
    u.onboundary = () => { state.talk = 1; };
    u.onend = u.onerror = () => { state.speaking = false; setState(state.listening ? "listening" : "ready"); pauseListening(false); };
    speechSynthesis.speak(u);
  }

  // ---------- Voice in ----------
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, wantListen = false, paused = false;

  function setState(s) { $("state").textContent = s; }

  function makeRecognizer(onText, continuous) {
    const r = new Recognition();
    r.lang = "en-US"; r.continuous = continuous; r.interimResults = false;
    r.onresult = (e) => { const res = e.results[e.results.length - 1]; if (res.isFinal) onText(res[0].transcript.trim()); };
    return r;
  }

  function startListening() {
    if (!Recognition) { note("Voice input isn't supported in this browser. Chrome, Edge and Safari support it; you can still type."); return; }
    wantListen = true;
    rec = makeRecognizer(onHeard, true);
    rec.onstart = () => { state.listening = true; setState("listening"); };
    rec.onend = () => { state.listening = false; if (wantListen && !paused) { try { rec.start(); } catch (e) { /* already started */ } } else setState("ready"); };
    rec.onerror = (e) => { if (e.error === "not-allowed") { wantListen = false; note("Microphone permission was refused."); } };
    try { rec.start(); } catch (e) { /* ignore */ }
    $("mic").setAttribute("aria-pressed", "true"); $("mic").textContent = "■ Stop";
  }
  function stopListening() {
    wantListen = false; if (rec) rec.stop();
    $("mic").setAttribute("aria-pressed", "false"); $("mic").textContent = "🎙 Listen"; setState("ready");
  }
  function pauseListening(p) { // don't hear her own voice
    paused = p;
    if (!rec || !wantListen) return;
    if (p) rec.stop(); else { try { rec.start(); } catch (e) { /* ignore */ } }
  }

  function onHeard(text) {
    if (!text) return;
    if ($("wake").checked) {
      const m = text.match(/\b(jarvis|jervis|travis)\b[\s,.:!?]*(.*)$/i);
      if (!m) return; // not for her
      text = m[2] || "hello";
    }
    ask(text);
  }

  // ---------- Server ----------
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  credentials: "same-origin", body: JSON.stringify(body) });
    let data = {}; try { data = await r.json(); } catch (e) { /* ignore */ }
    return { ok: r.ok, status: r.status, data };
  }

  function add(cls, html) {
    const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html;
    $("log").prepend(d);
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function note(text) { add("her", '<span class="err">' + esc(text) + "</span>"); }

  async function ask(q) {
    add("me", esc(q)); setState("thinking"); hud.thinking = 1;
    const r = await post("/api/jarvis/ask", { question: q });
    hud.thinking = 0;
    if (r.status === 403 || r.status === 401) { await refresh(); note(r.data.error || "Locked."); return; }
    if (!r.ok) { note(r.data.error || "Something went wrong."); setState("ready"); return; }
    add("her", "<b>JARVIS</b> " + esc(r.data.reply));
    speak(r.data.reply);
    if (!state.speaking) setState(state.listening ? "listening" : "ready");
  }

  async function unlock(pass) {
    $("lockMsg").textContent = "checking…";
    const r = await post("/api/jarvis/unlock", { passphrase: pass });
    $("pass").value = "";
    if (r.ok) { $("lockMsg").textContent = ""; await refresh(); speak("Hello. I'm here. What would you like to know?"); }
    else $("lockMsg").textContent = r.data.error || "That didn't work.";
  }

  async function refresh() {
    const r = await fetch("/api/jarvis", { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; return; }
    const s = await r.json();
    state.enabled = s.enabled; state.unlocked = s.unlocked;
    $("off").classList.toggle("hidden", s.enabled);
    $("offMsg").textContent = s.reason || "";
    $("setup").classList.toggle("hidden", !s.needs_setup);
    $("lock").classList.toggle("hidden", !s.enabled || s.unlocked || s.needs_setup);
    $("talk").classList.toggle("hidden", !s.unlocked);
    $("brain").textContent = s.unlocked ? (s.brain === "claude" ? "Brain: Claude" : "Brain: built-in handbook (add ANTHROPIC_API_KEY for Claude)") : "";
    if (!s.unlocked && wantListen) stopListening();
    setState(!s.enabled ? "offline" : s.unlocked ? "ready" : s.needs_setup ? "choose a passphrase" : "locked");
  }

  // ---------- Wire up ----------
  $("setupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = $("newPass").value, b = $("newPass2").value;
    if (a !== b) { $("setupMsg").textContent = "The two don't match."; return; }
    const r = await post("/api/jarvis/setup", { passphrase: a });
    $("newPass").value = $("newPass2").value = "";
    if (r.ok) { $("setupMsg").textContent = ""; await refresh(); speak("Thank you. I'll answer only to that phrase. What would you like to know?"); }
    else $("setupMsg").textContent = r.data.error || "That didn't work.";
  });
  $("unlockForm").addEventListener("submit", (e) => { e.preventDefault(); if ($("pass").value) unlock($("pass").value); });
  $("sayPass").addEventListener("click", () => {
    if (!Recognition) { $("lockMsg").textContent = "Voice isn't supported in this browser; type it instead."; return; }
    const r = makeRecognizer((t) => unlock(t), false);
    r.onstart = () => { $("lockMsg").textContent = "listening for your passphrase…"; state.listening = true; };
    r.onend = () => { state.listening = false; };
    r.start();
  });
  $("askForm").addEventListener("submit", (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q) { $("q").value = ""; ask(q); } });
  $("mic").addEventListener("click", () => (wantListen ? stopListening() : startListening()));
  $("lockBtn").addEventListener("click", async () => { stopListening(); await post("/api/jarvis/lock", {}); refresh(); });

  try { startHologram(); } catch (e) { $("state").textContent = "hologram unavailable"; }
  refresh();
})();

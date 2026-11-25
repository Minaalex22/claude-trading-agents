// FX3D: motion on every page.
//  - a live 3D background: six agent clusters (one per department) wired to the Conductor, pulses running
//    down the wires, a neon grid floor and stars; moves with the mouse, the phone's tilt and the scroll
//  - a glowing ticker tape of every market (from /api/markets)
//  - glass panels that tilt toward the pointer, slide in as you scroll, and numbers that count up and flash
//  - a big interactive 3D agent cluster on Agents (/conductor) and the Command desk (/)
// Phones get fewer points and no tilt; "reduce motion" draws one still frame.
"use strict";
(() => {
  const D = document, W = window, root = D.documentElement;
  root.classList.add("fx-js");
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const reduce = W.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const phone = W.matchMedia("(max-width: 860px)").matches || /Mobi|Android/i.test(navigator.userAgent);
  const finePointer = W.matchMedia("(pointer: fine)").matches;
  // pages that already have their own full 3D scene keep it to themselves
  const OWN_SCENE = new Set(["/3d", "/galaxy", "/jarvis"]);
  const DEPTS = [
    { k: "Entry", n: 350, c: "#ff5c00", note: "trend traders" },
    { k: "Scalper", n: 350, c: "#ffc400", note: "reversion" },
    { k: "News", n: 175, c: "#00e5ff", note: "read headlines" },
    { k: "Exit", n: 175, c: "#8b5cff", note: "call the end" },
    { k: "Risk", n: 175, c: "#a6ff00", note: "size trades" },
    { k: "Hedge", n: 175, c: "#ff2bd6", note: "shrink when wild" },
  ];

  // ---------- load three.js once ----------
  let threeP = null;
  function three() {
    if (W.THREE) return Promise.resolve(W.THREE);
    if (!threeP) threeP = new Promise((ok, no) => {
      const s = D.createElement("script"); s.src = "/vendor/three.min.js"; s.async = true;
      s.onload = () => ok(W.THREE); s.onerror = no; D.head.appendChild(s);
    });
    return threeP;
  }
  function dotTexture(T) {
    const c = D.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d");
    const r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.25, "rgba(255,255,255,.85)"); r.addColorStop(.6, "rgba(255,255,255,.18)"); r.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = r; g.fillRect(0, 0, 64, 64);
    const t = new T.CanvasTexture(c); return t;
  }
  function gauss() { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

  // Build the six clusters + conductor into a group. Returns parts that the animators drive.
  function buildClusters(T, opt) {
    const g = new T.Group(), tex = dotTexture(T);
    const ringR = opt.ringR, spread = opt.spread, per = opt.scale;
    const pos = [], col = [], size = [], cores = [];
    const lineP = [], lineC = [];
    const c3 = new T.Color();
    DEPTS.forEach((d, i) => {
      const a = (i / DEPTS.length) * Math.PI * 2;
      const core = new T.Vector3(Math.cos(a) * ringR, (i % 2 ? 1 : -1) * ringR * 0.18, Math.sin(a) * ringR);
      cores.push(core); c3.set(d.c);
      const count = Math.max(24, Math.round(d.n * per));
      for (let j = 0; j < count; j++) {
        const p = new T.Vector3(gauss() * spread, gauss() * spread * 0.8, gauss() * spread).add(core);
        pos.push(p.x, p.y, p.z);
        const k = 0.65 + Math.random() * 0.5; col.push(c3.r * k, c3.g * k, c3.b * k);
        // a few agents are wired to their department core
        if (j % opt.wireEvery === 0) { lineP.push(core.x, core.y, core.z, p.x, p.y, p.z); lineC.push(c3.r, c3.g, c3.b, c3.r * .2, c3.g * .2, c3.b * .2); }
      }
      // core to conductor, and core to next core
      const nx = DEPTS[(i + 1) % DEPTS.length];
      lineP.push(core.x, core.y, core.z, 0, 0, 0); lineC.push(c3.r, c3.g, c3.b, 1, .36, 0);
      const a2 = ((i + 1) / DEPTS.length) * Math.PI * 2, n3 = new T.Color(nx.c);
      lineP.push(core.x, core.y, core.z, Math.cos(a2) * ringR, ((i + 1) % 2 ? 1 : -1) * ringR * 0.18, Math.sin(a2) * ringR); lineC.push(c3.r, c3.g, c3.b, n3.r, n3.g, n3.b);
    });
    const pg = new T.BufferGeometry();
    pg.setAttribute("position", new T.Float32BufferAttribute(pos, 3));
    pg.setAttribute("color", new T.Float32BufferAttribute(col, 3));
    const pts = new T.Points(pg, new T.PointsMaterial({ size: opt.pt, map: tex, vertexColors: true, transparent: true, depthWrite: false, blending: T.AdditiveBlending, sizeAttenuation: true }));
    g.add(pts);
    const lg = new T.BufferGeometry();
    lg.setAttribute("position", new T.Float32BufferAttribute(lineP, 3));
    lg.setAttribute("color", new T.Float32BufferAttribute(lineC, 3));
    const lines = new T.LineSegments(lg, new T.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: opt.lineOp, blending: T.AdditiveBlending, depthWrite: false }));
    g.add(lines);
    // glowing cores + the conductor
    const coreSprites = DEPTS.map((d, i) => {
      const s = new T.Sprite(new T.SpriteMaterial({ map: tex, color: d.c, transparent: true, blending: T.AdditiveBlending, depthWrite: false }));
      s.position.copy(cores[i]); s.scale.setScalar(opt.core); g.add(s); return s;
    });
    const cond = new T.Sprite(new T.SpriteMaterial({ map: tex, color: "#ffffff", transparent: true, blending: T.AdditiveBlending, depthWrite: false }));
    cond.scale.setScalar(opt.core * 1.5); g.add(cond);
    const halo = new T.Sprite(new T.SpriteMaterial({ map: tex, color: "#ff5c00", transparent: true, opacity: .8, blending: T.AdditiveBlending, depthWrite: false }));
    halo.scale.setScalar(opt.core * 4); g.add(halo);
    // orbit rings around the conductor
    const rings = [];
    [[ringR * 0.42, "#ff5c00"], [ringR * 0.6, "#00e5ff"], [ringR * 1.25, "#ff2bd6"]].forEach(([r, c], i) => {
      const geo = new T.BufferGeometry().setFromPoints(new T.EllipseCurve(0, 0, r, r, 0, Math.PI * (i === 2 ? 2 : 1.6)).getPoints(160).map(p => new T.Vector3(p.x, 0, p.y)));
      const ring = new T.Line(geo, new T.LineBasicMaterial({ color: c, transparent: true, opacity: i === 2 ? .18 : .5, blending: T.AdditiveBlending }));
      ring.rotation.x = (i - 1) * 0.5; g.add(ring); rings.push(ring);
    });
    // pulses: votes running from a department core to the conductor
    const NP = opt.pulses, pp = new Float32Array(NP * 3), pc = new Float32Array(NP * 3);
    const pulse = [];
    for (let i = 0; i < NP; i++) pulse.push({ d: i % DEPTS.length, t: Math.random(), v: 0.25 + Math.random() * 0.5, back: Math.random() < .35 });
    const pgeo = new T.BufferGeometry();
    pgeo.setAttribute("position", new T.BufferAttribute(pp, 3)); pgeo.setAttribute("color", new T.BufferAttribute(pc, 3));
    const pulses = new T.Points(pgeo, new T.PointsMaterial({ size: opt.pt * 3.2, map: tex, vertexColors: true, transparent: true, depthWrite: false, blending: T.AdditiveBlending }));
    g.add(pulses);
    const dc = DEPTS.map(d => new T.Color(d.c));
    function step(dt) {
      for (let i = 0; i < NP; i++) {
        const q = pulse[i]; q.t += dt * q.v;
        if (q.t > 1) { q.t = 0; q.d = (Math.random() * DEPTS.length) | 0; q.back = Math.random() < .35; }
        const c = cores[q.d], t = q.back ? 1 - q.t : q.t, lift = Math.sin(t * Math.PI) * ringR * 0.12;
        pp[i * 3] = c.x * (1 - t); pp[i * 3 + 1] = c.y * (1 - t) + lift; pp[i * 3 + 2] = c.z * (1 - t);
        const col2 = q.back ? dc[0] : dc[q.d]; pc[i * 3] = col2.r; pc[i * 3 + 1] = col2.g; pc[i * 3 + 2] = col2.b;
      }
      pgeo.attributes.position.needsUpdate = true; pgeo.attributes.color.needsUpdate = true;
    }
    return { group: g, cores, coreSprites, cond, halo, rings, step, tex, pts };
  }

  // ---------- full-page background ----------
  function startBackground() {
    if (OWN_SCENE.has(path)) return;
    const cv = D.createElement("canvas"); cv.id = "fx3d-bg"; cv.setAttribute("aria-hidden", "true");
    const vig = D.createElement("div"); vig.id = "fx3d-vig";
    D.body.prepend(vig); D.body.prepend(cv);
    three().then(T => {
      let renderer;
      try { renderer = new T.WebGLRenderer({ canvas: cv, antialias: !phone, alpha: true, powerPreference: "low-power" }); } catch (e) { cv.remove(); return; }
      renderer.setPixelRatio(Math.min(W.devicePixelRatio || 1, phone ? 1.25 : 1.6));
      const scene = new T.Scene(); scene.fog = new T.FogExp2(0x000000, 0.006);
      const cam = new T.PerspectiveCamera(58, 1, 0.1, 600);
      const cl = buildClusters(T, { ringR: 34, spread: 6.5, scale: phone ? 0.45 : 1.1, wireEvery: phone ? 8 : 5, pt: phone ? 1.7 : 1.45, lineOp: .45, core: 8, pulses: phone ? 26 : 70 });
      cl.group.position.set(phone ? 0 : 30, 10, -24); scene.add(cl.group);
      // neon floor grid that rolls toward you
      const grid = new T.GridHelper(600, 120, 0xff5c00, 0x00e5ff);
      grid.material.transparent = true; grid.material.opacity = .16; grid.material.blending = T.AdditiveBlending; grid.material.depthWrite = false;
      grid.position.y = -34; scene.add(grid);
      // stars
      const SN = phone ? 500 : 1400, sp = new Float32Array(SN * 3), sc = new Float32Array(SN * 3), pal = ["#ffffff", "#00e5ff", "#ff2bd6", "#ff5c00", "#a6ff00"].map(c => new T.Color(c));
      for (let i = 0; i < SN; i++) {
        sp[i * 3] = (Math.random() - .5) * 520; sp[i * 3 + 1] = (Math.random() - .3) * 260; sp[i * 3 + 2] = -Math.random() * 380 + 60;
        const c = pal[Math.random() < .6 ? 0 : 1 + ((Math.random() * 4) | 0)]; sc[i * 3] = c.r; sc[i * 3 + 1] = c.g; sc[i * 3 + 2] = c.b;
      }
      const sg = new T.BufferGeometry(); sg.setAttribute("position", new T.BufferAttribute(sp, 3)); sg.setAttribute("color", new T.BufferAttribute(sc, 3));
      const stars = new T.Points(sg, new T.PointsMaterial({ size: 1.1, map: cl.tex, vertexColors: true, transparent: true, opacity: .8, depthWrite: false, blending: T.AdditiveBlending }));
      scene.add(stars);
      // a slow wireframe icosahedron "core" far back
      const ico = new T.Mesh(new T.IcosahedronGeometry(22, 1), new T.MeshBasicMaterial({ color: 0xff2bd6, wireframe: true, transparent: true, opacity: .08, blending: T.AdditiveBlending }));
      ico.position.set(phone ? 0 : -60, 18, -120); scene.add(ico);

      let mx = 0, my = 0, tx = 0, ty = 0, scrollY = W.scrollY;
      W.addEventListener("pointermove", e => { tx = (e.clientX / innerWidth - .5) * 2; ty = (e.clientY / innerHeight - .5) * 2; }, { passive: true });
      W.addEventListener("deviceorientation", e => { if (e.gamma == null) return; tx = Math.max(-1, Math.min(1, e.gamma / 30)); ty = Math.max(-1, Math.min(1, (e.beta - 45) / 30)); }, { passive: true });
      W.addEventListener("scroll", () => { scrollY = W.scrollY; }, { passive: true });
      function size() { const w = innerWidth, h = innerHeight; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); }
      size(); W.addEventListener("resize", size);
      let last = performance.now(), running = true, t = 0;
      function frame(now) {
        if (!running) return;
        const dt = Math.min(.05, (now - last) / 1000); last = now; t += dt;
        mx += (tx - mx) * .04; my += (ty - my) * .04;
        const sy = Math.min(scrollY, 3000) / 3000;
        cam.position.set(mx * 10, 6 - my * 5 - sy * 14, 70 - sy * 26);
        cam.lookAt(cl.group.position.x * .4, cl.group.position.y - sy * 10, -30);
        cl.group.rotation.y += dt * 0.07; cl.group.rotation.x = Math.sin(t * .2) * .12 + .18;
        cl.rings.forEach((r, i) => { r.rotation.z += dt * (i % 2 ? -.3 : .22); });
        cl.step(dt);
        const pulseK = 1 + Math.sin(t * 3) * .15; cl.cond.scale.setScalar(10.5 * pulseK); cl.halo.material.opacity = .45 + Math.sin(t * 2) * .2;
        cl.coreSprites.forEach((s, i) => s.scale.setScalar(7 * (1 + Math.sin(t * 2.2 + i) * .18)));
        grid.position.z = (t * 6) % 5; stars.rotation.y = t * .004; ico.rotation.x += dt * .05; ico.rotation.y += dt * .08;
        renderer.render(scene, cam);
        if (!reduce) requestAnimationFrame(frame);
      }
      D.addEventListener("visibilitychange", () => { const vis = !D.hidden; if (vis && !running && !reduce) { running = true; last = performance.now(); requestAnimationFrame(frame); } else if (!vis) running = false; });
      requestAnimationFrame(frame);
      requestAnimationFrame(() => cv.classList.add("on"));
    }).catch(() => cv.remove());
  }

  // ---------- big interactive cluster (Agents + Command desk) ----------
  function startClusterHero() {
    if (path !== "/conductor" && path !== "/") return;
    const anchor = D.querySelector(".pg-head") || D.querySelector("main");
    if (!anchor) return;
    const box = D.createElement("section"); box.className = "fx-panel fx-cluster";
    const total = DEPTS.reduce((a, d) => a + d.n, 0);
    box.innerHTML = `<div class="fx-cl-top"><h3 class="fx-h">Live agent clusters <span>drag to spin · votes fly to the Conductor</span></h3>
      <div class="fx-cl-n">${total.toLocaleString()}<small>AGENTS · 6 DEPARTMENTS</small></div></div>
      <div class="fx-legend">${DEPTS.map(d => `<span><i></i>${d.k}<b>${d.n}</b></span>`).join("")}</div><div class="fx-vote"></div>`;
    // the page's CSP blocks inline style attributes, so colours go on through the CSSOM
    box.querySelectorAll(".fx-legend span").forEach((el, i) => el.style.setProperty("--c", DEPTS[i].c));
    if (anchor.classList.contains("pg-head")) anchor.after(box); else anchor.prepend(box);
    if (path === "/") box.classList.add("fx-home");
    three().then(T => {
      const cv = D.createElement("canvas"); box.prepend(cv);
      let renderer; try { renderer = new T.WebGLRenderer({ canvas: cv, antialias: true, alpha: true }); } catch (e) { return; }
      renderer.setPixelRatio(Math.min(W.devicePixelRatio || 1, phone ? 1.5 : 2));
      const scene = new T.Scene(), cam = new T.PerspectiveCamera(50, 1, .1, 500);
      const cl = buildClusters(T, { ringR: 26, spread: 4.2, scale: phone ? .7 : 1.4, wireEvery: 4, pt: phone ? 1.1 : .9, lineOp: .42, core: 6, pulses: phone ? 40 : 110 });
      scene.add(cl.group);
      const labels = DEPTS.map(d => { const el = D.createElement("div"); el.className = "fx-lbl"; el.style.setProperty("--c", d.c); el.textContent = d.k.toUpperCase(); box.appendChild(el); return el; });
      const condL = D.createElement("div"); condL.className = "fx-lbl"; condL.style.setProperty("--c", "#ff5c00"); condL.textContent = "CONDUCTOR"; box.appendChild(condL);
      let rotY = 0, rotX = .35, vY = .12, drag = null;
      cv.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); });
      cv.addEventListener("pointermove", e => { if (!drag) return; vY = (e.clientX - drag.x) * .004; rotX = Math.max(-.2, Math.min(1.1, rotX + (e.clientY - drag.y) * .004)); rotY += vY; drag = { x: e.clientX, y: e.clientY }; });
      cv.addEventListener("pointerup", () => { drag = null; });
      cv.style.touchAction = "pan-y";
      function size() { const r = box.getBoundingClientRect(); renderer.setSize(r.width, r.height, false); cam.aspect = r.width / r.height; cam.updateProjectionMatrix(); }
      size(); W.addEventListener("resize", size);
      const v = new T.Vector3(), voteBox = box.querySelector(".fx-vote");
      const MK = ["BTC", "ETH", "NVDA", "AAPL", "TSLA", "GOLD", "EURUSD", "USDJPY", "SPX", "META"];
      let visible = true; new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(box);
      let last = performance.now(), t = 0, nextVote = 0;
      function frame(now) {
        const dt = Math.min(.05, (now - last) / 1000); last = now;
        if (visible && !D.hidden) {
          t += dt; if (!drag) { rotY += vY * dt * 8; vY += (.012 - vY) * .02; }
          cl.group.rotation.set(rotX, rotY, 0);
          cam.position.set(0, 4, phone ? 84 : 60); cam.lookAt(0, 0, 0);
          cl.rings.forEach((r, i) => { r.rotation.z += dt * (i % 2 ? -.35 : .25); });
          cl.step(dt);
          cl.cond.scale.setScalar(9 * (1 + Math.sin(t * 3) * .15)); cl.halo.material.opacity = .5 + Math.sin(t * 2) * .25;
          cl.coreSprites.forEach((s, i) => s.scale.setScalar(6 * (1 + Math.sin(t * 2.4 + i) * .2)));
          const r = box.getBoundingClientRect();
          cl.cores.forEach((c, i) => { v.copy(c).applyMatrix4(cl.group.matrixWorld).project(cam); labels[i].style.left = ((v.x + 1) / 2 * r.width) + "px"; labels[i].style.top = ((1 - v.y) / 2 * r.height) + "px"; labels[i].style.opacity = v.z < 1 ? 1 : 0; });
          v.set(0, 0, 0).applyMatrix4(cl.group.matrixWorld).project(cam); condL.style.left = ((v.x + 1) / 2 * r.width) + "px"; condL.style.top = ((1 - v.y) / 2 * r.height) + "px";
          if (t > nextVote && voteBox) {
            nextVote = t + 1.1 + Math.random() * 1.2;
            const d = DEPTS[(Math.random() * DEPTS.length) | 0], side = Math.random() < .5 ? "BUY" : "SELL";
            const row = D.createElement("div"); row.style.setProperty("--c", d.c);
            row.innerHTML = `<b>${d.k}</b> · ${MK[(Math.random() * MK.length) | 0]} ${side} vote → Conductor`;
            voteBox.appendChild(row); while (voteBox.children.length > 4) voteBox.firstChild.remove();
          }
          cl.group.updateMatrixWorld(); renderer.render(scene, cam);
        }
        if (!reduce) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    }).catch(() => {});
  }

  // ---------- ticker tape ----------
  function startTape() {
    const bar = D.querySelector(".ats-bar"); if (!bar || OWN_SCENE.has(path)) return;
    const tape = D.createElement("div"); tape.id = "fx-tape"; tape.setAttribute("aria-label", "Live prices");
    bar.after(tape);
    const setTop = () => root.style.setProperty("--fx-bar-h", bar.getBoundingClientRect().height + "px");
    setTop(); W.addEventListener("resize", setTop);
    const fmt = p => p >= 1000 ? p.toLocaleString(undefined, { maximumFractionDigits: 0 }) : p >= 10 ? p.toFixed(2) : p.toFixed(4);
    function spark(arr, up) {
      if (!arr || arr.length < 2) return "";
      const a = arr.slice(-24), lo = Math.min(...a), hi = Math.max(...a), k = hi - lo || 1;
      const d = a.map((y, i) => (i ? "L" : "M") + (i * 46 / (a.length - 1)).toFixed(1) + "," + (16 - (y - lo) / k * 16).toFixed(1)).join("");
      return `<svg viewBox="0 0 46 16" class="${up ? "u" : "d"}"><path d="${d}" stroke="currentColor"/></svg>`;
    }
    function load() {
      fetch("/api/markets", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).then(j => {
        const ps = (j && j.products || []).filter(p => p.price).slice(0, 40);
        if (!ps.length) { tape.remove(); return; }
        const one = ps.map(p => {
          const ch = +(p.change_1d ?? p.change_5m ?? 0), up = ch >= 0;
          return `<a href="/markets/${encodeURIComponent(p.symbol)}"><b>${p.symbol}</b>${fmt(+p.price)}<span class="${up ? "u" : "d"}">${up ? "▲" : "▼"} ${Math.abs(ch).toFixed(2)}%</span>${spark(p.spark, up)}</a>`;
        }).join("");
        tape.innerHTML = `<div class="fx-run">${one}${one}</div>`;
        tape.style.setProperty("--fx-tape-s", Math.max(30, ps.length * 4) + "s");
      }).catch(() => tape.remove());
    }
    load(); setInterval(() => { if (!D.hidden) load(); }, 60000);
  }

  // ---------- panels: reveal, tilt, counting numbers, chart draw ----------
  const PANELS = ".pg-card, .card, .kpi, .mk-card, .oa-p, .oa-kpi, .dd-card, .dd-stat, .pg-tile, .fx-panel";
  function startPanels() {
    const io = "IntersectionObserver" in W ? new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("fx-in"); io.unobserve(e.target); } }), { rootMargin: "0px 0px -6% 0px" }) : null;
    let n = 0; const born = performance.now();
    function tag(el) {
      if (el.__fx) return; el.__fx = 1;
      // panels the page rebuilds every few seconds must not replay the entrance
      if (io && !reduce && performance.now() - born < 1500) { el.classList.add("fx-rv"); el.style.setProperty("--fx-d", Math.min(n++ * 0.06, .6) + "s"); io.observe(el); }
      if (finePointer && !phone && !reduce) tilt(el);
    }
    D.querySelectorAll(PANELS).forEach(tag);
    new MutationObserver(ms => { for (const m of ms) for (const nd of m.addedNodes) if (nd.nodeType === 1) { if (nd.matches(PANELS)) tag(nd); nd.querySelectorAll && nd.querySelectorAll(PANELS).forEach(tag); } })
      .observe(D.body, { childList: true, subtree: true });
    // safety: nothing stays hidden
    setTimeout(() => D.querySelectorAll(".fx-rv:not(.fx-in)").forEach(el => { const r = el.getBoundingClientRect(); if (r.top < innerHeight) el.classList.add("fx-in"); }), 2500);
  }
  function tilt(el) {
    el.classList.add("fx-tilt");
    let raf = 0;
    el.addEventListener("pointermove", e => {
      const r = el.getBoundingClientRect(); if (r.width > 900) { el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px"); return; }
      const px = (e.clientX - r.left) / r.width - .5, py = (e.clientY - r.top) / r.height - .5;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        el.style.transform = `perspective(1000px) rotateX(${(-py * 6).toFixed(2)}deg) rotateY(${(px * 7).toFixed(2)}deg) translateZ(6px)`;
        el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px");
      });
    });
    el.addEventListener("pointerleave", () => { cancelAnimationFrame(raf); el.style.transform = ""; });
  }
  // numbers count from the old value to the new one and flash
  const NUM = /([-+−]?)(\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?/;
  function startCounters() {
    const sel = ".pg-kpi b, .kpi b, .oa-kpi b, .dd-stat b, .fx-count";
    const mine = new WeakMap();
    function parse(s) { const m = NUM.exec(s || ""); if (!m) return null; const v = parseFloat((m[3] + (m[4] || "")).replace(/,/g, "")) * (m[1] === "-" || m[1] === "−" ? -1 : 1); return { m, v, dec: m[4] ? m[4].length - 1 : 0, comma: m[3].includes(",") || Math.abs(v) >= 10000 }; }
    function fmt(p, x) {
      const a = Math.abs(x); let s = a.toFixed(p.dec); if (p.comma) { const [i, f] = s.split("."); s = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (f ? "." + f : ""); }
      const sign = p.m[1] === "+" ? (x < 0 ? "-" : "+") : (x < 0 ? "-" : "");
      return sign + p.m[2] + s;
    }
    function run(el, from, to) {
      const txt = to; const pt = parse(txt), pf = parse(from);
      el.classList.remove("fx-flash"); void el.offsetWidth; el.classList.add("fx-flash");
      if (!pt || reduce) return;
      const start = pf ? pf.v : 0; if (start === pt.v) return;
      const t0 = performance.now(), dur = 900, pre = txt.slice(0, pt.m.index), post = txt.slice(pt.m.index + pt.m[0].length);
      (function f(now) {
        if (el.textContent !== mine.get(el) && mine.has(el)) return; // page wrote a newer value
        const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
        const s = k < 1 ? pre + fmt(pt, start + (pt.v - start) * e) + post : txt;
        mine.set(el, s); el.textContent = s;
        if (k < 1) requestAnimationFrame(f); else mine.delete(el);
      })(t0);
    }
    const seen = new WeakMap();
    function watch(el) {
      if (seen.has(el)) return; seen.set(el, el.textContent);
      new MutationObserver(() => {
        const now = el.textContent; if (mine.get(el) === now) return;
        const before = seen.get(el); seen.set(el, now);
        if (now !== before && el.children.length === 0) run(el, before === "–" || before === "-" ? "" : before, now);
      }).observe(el, { childList: true, characterData: true, subtree: true });
    }
    D.querySelectorAll(sel).forEach(watch);
    new MutationObserver(ms => { for (const m of ms) for (const nd of m.addedNodes) if (nd.nodeType === 1 && nd.querySelectorAll) nd.querySelectorAll(sel).forEach(watch); }).observe(D.body, { childList: true, subtree: true });
  }
  // SVG chart lines draw themselves in the first time they appear
  function startDraw() {
    if (reduce) return;
    const done = new WeakSet();
    function go(svg) {
      if (done.has(svg) || svg.closest("#fx-tape")) return; done.add(svg);
      const ps = svg.querySelectorAll("path, polyline"); if (!ps.length || ps.length > 40) return;
      ps.forEach(p => { try { const L = p.getTotalLength(); if (L > 40) { p.style.setProperty("--fx-len", L); } } catch (e) {} });
      svg.classList.add("fx-draw"); setTimeout(() => svg.classList.remove("fx-draw"), 1800);
    }
    setTimeout(() => D.querySelectorAll(".pg-card svg, .card svg, .mk-card svg").forEach(go), 600);
  }

  function boot() {
    try { startBackground(); } catch (e) { /* the page still works without it */ }
    try { startTape(); } catch (e) {}
    try { startClusterHero(); } catch (e) {}
    try { startPanels(); } catch (e) {}
    try { startCounters(); } catch (e) {}
    try { startDraw(); } catch (e) {}
  }
  if (D.readyState === "loading") D.addEventListener("DOMContentLoaded", boot); else boot();
})();

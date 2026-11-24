// APEX: JARVIS's humanoid avatar. A head-and-shoulders bust made of glowing particles with a wireframe skin,
// drawn on the JARVIS stage canvas (plain 2D canvas, no libraries). Our own look: cyan glow, orange accents.
// States: intro (particles spiral up from one dot and build the bust, left side first), ready, listening,
// thinking, speaking (pulses with her voice), locked/offline (dim orange) and a pinch reaction (shockwave).
(function () {
  "use strict";
  const CYAN = "63,224,255", ORANGE = "255,92,0", ICE = "200,245,255";

  // ---- Bust geometry: rings stacked bottom to top. Units: y up, bust about 2 tall, centre near 0. ----
  // Front of the face is -z (towards the viewer). No eyes or mouth are drawn, only the shape of a face.
  const smooth = (e0, e1, x) => { const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
  const bump = (x, c, w) => Math.exp(-((x - c) * (x - c)) / (2 * w * w));
  function profile(y) { // [rx, rz, zShift] of the cross-section at height y, or null outside the bust
    if (y > 1.0 || y < -0.95) return null;
    if (y >= 0.27) { // skull and face: round crown, wider at the temples, a chin, back of the skull further back
      const c = 0.61, hy = 0.37, u = (y - c) / hy;
      const s = Math.pow(Math.max(0, 1 - u * u), u > 0 ? 0.42 : 0.5);
      const jaw = u < 0 ? 1 - 0.3 * Math.pow(-u, 1.8) : 1;
      const neck = u < 0 ? smooth(-0.6, -0.95, u) : 0; // the jaw flows into the neck instead of pinching to a point
      return [Math.max(0.27 * s * jaw, 0.16 * neck), Math.max(0.31 * s * (u < 0 ? 1 - 0.2 * Math.pow(-u, 2) : 1), 0.18 * neck), 0.03 + (u < 0 ? 0.04 * -u : 0)];
    }
    if (y >= 0.04) { // short neck, leaning slightly forward, tucked under the jaw
      const k = smooth(0.2, 0.27, y);
      return [0.15 + 0.02 * k, 0.16 + 0.03 * k, 0.05];
    }
    // trapezius (nearly flat), rounded shoulder caps, then the upper arms and chest fading out at the bottom
    const d = 0.04 - y;
    let rx;
    if (d < 0.1) rx = 0.16 + 0.46 * Math.sin((d / 0.1) * Math.PI / 2);
    else if (d < 0.34) rx = 0.62 + 0.18 * Math.sin(((d - 0.1) / 0.24) * Math.PI / 2);
    else rx = 0.8 - 0.07 * smooth(0.34, 1.0, d);
    const rz = 0.14 + 0.15 * smooth(0, 0.2, d);
    return [rx, rz, 0.03];
  }
  function faceDepth(x, y) { // extra depth on the front of the head: brow, eye hollows, nose, lips, chin
    let z = 0;
    z -= 0.035 * bump(y, 0.72, 0.03) * bump(x, 0, 0.13);
    z += 0.03 * bump(y, 0.645, 0.035) * (bump(x, -0.085, 0.035) + bump(x, 0.085, 0.035));
    z -= 0.075 * bump(y, 0.58, 0.05) * bump(x, 0, 0.022) * smooth(0.5, 0.56, y);
    z -= 0.02 * bump(y, 0.45, 0.02) * bump(x, 0, 0.05);
    z -= 0.03 * bump(y, 0.36, 0.035) * bump(x, 0, 0.06);
    return z;
  }
  function chestDepth(x, y) { return y < -0.2 && y > -0.75 ? -0.05 * bump(y, -0.45, 0.13) * (bump(x, -0.22, 0.14) + bump(x, 0.22, 0.14)) : 0; }

  function surface(y, a, rx, rz, zs) {
    let x = Math.cos(a) * rx, z = Math.sin(a) * rz;
    if (y < 0.06 && y > -0.95) { const sq = Math.sign(z) * Math.pow(Math.abs(Math.sin(a)), 0.75) * rz; z = sq; } // flatter chest and back
    if (z < 0) { const f = Math.min(1, -Math.sin(a) * 1.4); z += (y >= 0.3 ? faceDepth(x, y) : chestDepth(x, y)) * f; }
    return [x, z + zs];
  }

  function buildBust() {
    const pts = [], rings = [];
    const step = 0.03;
    for (let y = -0.94; y <= 0.995; y += step) {
      const p = profile(y); if (!p) continue;
      const [rx, rz, zs] = p, circ = Math.PI * (rx + rz);
      const n = Math.max(8, Math.round(circ * 60));
      const ring = [];
      for (let i = 0; i < n; i++) {
        const a = ((i + 0.5) / n) * Math.PI * 2;
        const [x, z] = surface(y, a, rx, rz, zs);
        ring.push(pts.length);
        pts.push({ x, y: y + (Math.random() - 0.5) * step * 0.8, z, ring: rings.length });
      }
      rings.push(ring);
    }
    // loose sparkles on the surface: they shimmer and break up the grid so it reads as light, not a coil
    const grid = pts.length;
    for (let i = 0; i < 1600; i++) {
      const y = -0.93 + Math.random() * 1.92, p = profile(y); if (!p) continue;
      const a = Math.random() * Math.PI * 2, [x, z] = surface(y, a, p[0], p[1], p[2]), k = 1 + (Math.random() - 0.5) * 0.05;
      pts.push({ x: x * k, y, z: z * k, ring: -1, sparkle: true });
    }
    const meridians = [];
    const M = 24;
    for (let m = 0; m < M; m++) {
      const line = [];
      for (const ring of rings) line.push(ring[Math.floor(((m + 0.5) / M) * ring.length) % ring.length]);
      meridians.push(line);
    }
    return { pts, rings, meridians, grid };
  }

  function create() {
    const bust = buildBust();
    const P = bust.pts;
    const minX = -0.95, maxX = 0.95;
    for (const p of P) { // intro schedule: left side first, then upward
      const side = (p.x - minX) / (maxX - minX);
      p.delay = 0.45 + side * 1.0 + (p.y + 1) * 0.18 + Math.random() * 0.25;
      p.spin = Math.random() * Math.PI * 2;
      p.phase = Math.random() * Math.PI * 2;
      p.vx = 0; p.vy = 0; p.ox = 0; p.oy = 0; // burst offsets in screen space
    }
    const proj = new Float32Array(P.length * 3);
    let clock = 0, introT = 0, pinchT = 9, scan = 0, look = 0, mood = { glow: 0.6, rgb: CYAN };
    const shock = [];

    function ease(x) { return x <= 0 ? 0 : x >= 1 ? 1 : 1 - Math.pow(1 - x, 3); }

    function draw(ctx, W, H, dt, s) {
      // s = { mode, talk, mx, my, hand }
      clock += dt; introT += dt; pinchT += dt;
      const live = s.mode !== "locked" && s.mode !== "offline";
      const targetGlow = s.mode === "speaking" ? 0.85 + s.talk * 0.4 : s.mode === "listening" ? 0.9 : s.mode === "thinking" ? 0.8 : live ? 0.65 : 0.35;
      mood.glow += (targetGlow - mood.glow) * Math.min(1, dt * 4);
      const rgb = live ? CYAN : ORANGE;
      const S = Math.min(H * 0.36, W * 0.42), cx = W / 2, cy = H * 0.5;
      const breathe = 1 + Math.sin(clock * 1.4) * 0.008 + (s.mode === "speaking" ? s.talk * 0.012 : 0);
      // head follows the pointer or the tracked hand a little
      const lookX = s.hand ? (s.hand.x - 0.5) * 1.2 : s.mx * 0.9;
      look += (lookX - look) * Math.min(1, dt * 3);
      const yaw = Math.sin(clock * 0.35) * 0.28 + look + (s.mode === "listening" ? Math.sin(clock * 0.9) * 0.05 : 0);
      const pitch = 0.06 + (s.my || 0) * 0.15 + (s.mode === "thinking" ? Math.sin(clock * 0.7) * 0.05 : 0);
      const cyw = Math.cos(yaw), syw = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const F = 3.2;
      const speakAmp = s.mode === "speaking" ? s.talk : 0;
      if (s.mode === "thinking") scan = (scan + dt * 0.9) % 1; else scan = 0;
      const scanY = 1.05 - scan * 2.0;

      // project
      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        let x = p.x, y = p.y, z = p.z;
        if (speakAmp > 0 && y > 0.25) { // the head swells in waves while she talks, strongest near the jaw
          const w = 1 + speakAmp * 0.06 * Math.max(0, Math.sin((0.8 - y) * 9 - clock * 14));
          x *= w; z *= w;
        }
        if (p.sparkle) { const j = 0.012 * Math.sin(clock * 3 + p.phase); x += j; z += j; }
        const x1 = x * cyw + z * syw, z1 = -x * syw + z * cyw;
        const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
        const k = F / (F + z2) * breathe;
        proj[i * 3] = cx + x1 * S * k;
        proj[i * 3 + 1] = cy - y2 * S * k;
        proj[i * 3 + 2] = z2; // negative = towards the viewer
      }

      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.shadowBlur = 0;

      // base glow under the bust
      const bg = ctx.createRadialGradient(cx, cy + S * 0.95, 0, cx, cy + S * 0.95, S * 1.1);
      bg.addColorStop(0, "rgba(" + rgb + "," + 0.18 * mood.glow + ")"); bg.addColorStop(1, "rgba(" + rgb + ",0)");
      ctx.fillStyle = bg; ctx.fillRect(cx - S * 1.2, cy - S * 0.2, S * 2.4, S * 1.6);
      const hg = ctx.createRadialGradient(cx, cy - S * 0.62, 0, cx, cy - S * 0.62, S * 0.7);
      hg.addColorStop(0, "rgba(" + rgb + "," + 0.12 * mood.glow + ")"); hg.addColorStop(1, "rgba(" + rgb + ",0)");
      ctx.fillStyle = hg; ctx.fillRect(cx - S, cy - S * 1.4, S * 2, S * 1.6);

      // intro: a single dot at the bottom, then particles fly up in a spiral
      const introDone = introT > 3.2;
      if (introT < 0.6) {
        const r = 3 + Math.sin(introT * 20) * 1.5;
        ctx.fillStyle = "rgba(" + ICE + ",0.95)"; ctx.beginPath(); ctx.arc(cx, H - 24, r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(" + CYAN + ",0.25)"; ctx.beginPath(); ctx.arc(cx, H - 24, r * 4, 0, Math.PI * 2); ctx.fill();
      }

      // wireframe skin: fades in after the particles land
      const wire = ease((introT - 1.9) / 1.1) * (0.1 + 0.12 * mood.glow);
      if (wire > 0.01) {
        ctx.lineWidth = 0.8;
        for (let r = 1; r < bust.rings.length; r += 4) {
          const ring = bust.rings[r], ry = P[ring[0]].y;
          const fade = ry < -0.45 ? Math.max(0, (ry + 0.95) / 0.5) : 1;
          if (fade <= 0) continue;
          ctx.beginPath();
          for (let j = 0; j <= ring.length; j++) {
            const i = ring[j % ring.length], X = proj[i * 3], Y = proj[i * 3 + 1];
            if (j === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
          }
          ctx.strokeStyle = "rgba(" + rgb + "," + wire * fade + ")"; ctx.stroke();
        }
        for (const line of bust.meridians) {
          for (let j = 1; j < line.length; j++) {
            const a = line[j - 1], b = line[j];
            const front = proj[a * 3 + 2] + proj[b * 3 + 2] < 0, fy = P[a].y, fade = fy < -0.45 ? Math.max(0, (fy + 0.95) / 0.5) : 1;
            ctx.strokeStyle = "rgba(" + rgb + "," + (front ? wire * 1.3 : wire * 0.35) * fade + ")";
            ctx.beginPath(); ctx.moveTo(proj[a * 3], proj[a * 3 + 1]); ctx.lineTo(proj[b * 3], proj[b * 3 + 1]); ctx.stroke();
          }
        }
      }

      // particles
      const burst = pinchT < 1.6;
      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        let X = proj[i * 3], Y = proj[i * 3 + 1];
        const z = proj[i * 3 + 2];
        let a = (z < 0 ? 0.55 + Math.min(0.45, -z * 1.2) : Math.max(0.08, 0.4 - z * 0.8)) * mood.glow;
        if (!introDone) {
          const e = ease((introT - p.delay) / 1.0);
          if (e <= 0) continue;
          const sx = cx, sy = H - 24, th = p.spin + (1 - e) * 7, rad = (1 - e) * S * 0.6;
          X = sx + (X - sx) * e + Math.cos(th) * rad;
          Y = sy + (Y - sy) * e + Math.sin(th) * rad * 0.35;
          a *= 0.4 + 0.6 * e;
        }
        if (burst) { // pinch: particles kick outwards, then spring home
          if (pinchT < dt * 1.5) { const dx = X - cx, dy = Y - (cy - S * 0.3), d = Math.hypot(dx, dy) || 1; const f = 260 + Math.random() * 200; p.vx = dx / d * f; p.vy = dy / d * f; }
          p.vx += -p.ox * 40 * dt; p.vy += -p.oy * 40 * dt; p.vx *= 0.9; p.vy *= 0.9;
          p.ox += p.vx * dt; p.oy += p.vy * dt;
        } else { p.ox *= 0.85; p.oy *= 0.85; }
        X += p.ox; Y += p.oy;
        if (p.y < -0.45) a *= Math.max(0, (p.y + 0.95) / 0.5);
        let col = rgb;
        if (s.mode === "thinking" && Math.abs(p.y - scanY) < 0.06) { col = ORANGE; a = Math.min(1, a + 0.5); }
        if (p.sparkle) a *= 0.5 + 0.5 * Math.sin(clock * 4 + p.phase);
        const size = p.sparkle ? 1.1 : (z < 0 ? 1.7 : 1.1);
        ctx.fillStyle = "rgba(" + col + "," + a.toFixed(3) + ")";
        ctx.fillRect(X - size / 2, Y - size / 2, size, size);
      }

      // thinking: an orange scan band crosses the head
      if (s.mode === "thinking") {
        const y = cy - scanY * S;
        const g = ctx.createLinearGradient(0, y - 14, 0, y + 14);
        g.addColorStop(0, "rgba(" + ORANGE + ",0)"); g.addColorStop(0.5, "rgba(" + ORANGE + ",0.22)"); g.addColorStop(1, "rgba(" + ORANGE + ",0)");
        ctx.fillStyle = g; ctx.fillRect(cx - S * 0.9, y - 14, S * 1.8, 28);
      }

      // a halo ring around the base of the head; listening adds sound bars, speaking pulses it
      const hx = cx, hy = cy + S * 0.9, hr = S * 0.95;
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = "rgba(" + rgb + "," + (0.25 * mood.glow + speakAmp * 0.4) + ")";
      ctx.beginPath(); ctx.ellipse(hx, hy, hr * (1 + speakAmp * 0.06), hr * 0.12, 0, 0, Math.PI * 2); ctx.stroke();
      if (s.mode === "listening" || s.mode === "speaking") {
        const n = 64;
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const t = (i / n) * Math.PI * 2;
          const amp = s.mode === "speaking" ? speakAmp * (0.4 + 0.6 * Math.abs(Math.sin(i * 0.9 + clock * 11)))
            : 0.25 + 0.25 * Math.abs(Math.sin(i * 1.7 + clock * 6));
          const x = hx + Math.cos(t) * hr * 1.06, y = hy + Math.sin(t) * hr * 0.13;
          ctx.moveTo(x, y); ctx.lineTo(x, y - 4 - amp * S * 0.12);
        }
        ctx.strokeStyle = "rgba(" + rgb + ",0.55)"; ctx.stroke();
      }
      // orange accent node at the collarbone (our mark, not a face)
      const nodeA = live ? 0.55 + 0.35 * Math.sin(clock * 2) + speakAmp * 0.3 : 0.4;
      const ng = ctx.createRadialGradient(cx, cy + S * 0.22, 0, cx, cy + S * 0.22, S * 0.09);
      ng.addColorStop(0, "rgba(255,190,140," + nodeA + ")"); ng.addColorStop(0.4, "rgba(" + ORANGE + "," + nodeA * 0.7 + ")"); ng.addColorStop(1, "rgba(" + ORANGE + ",0)");
      ctx.fillStyle = ng; ctx.beginPath(); ctx.arc(cx, cy + S * 0.22, S * 0.09, 0, Math.PI * 2); ctx.fill();

      // intro finish and pinch: expanding shockwave rings
      if (introT - dt < 3.0 && introT >= 3.0) shock.push({ t: 0, rgb: CYAN });
      for (let i = shock.length - 1; i >= 0; i--) {
        const w = shock[i]; w.t += dt;
        if (w.t > 1.2) { shock.splice(i, 1); continue; }
        const r = S * (0.3 + w.t * 1.4);
        ctx.strokeStyle = "rgba(" + w.rgb + "," + (1 - w.t / 1.2) * 0.6 + ")"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(cx, cy - S * 0.3, r, r * 0.9, 0, 0, Math.PI * 2); ctx.stroke();
      }

      // the tracked hand: a cursor ring that fills as the fingers close
      if (s.hand) {
        const x = s.hand.x * W, y = s.hand.y * H, close = Math.max(0, Math.min(1, s.hand.closeness));
        ctx.strokeStyle = "rgba(" + ORANGE + ",0.9)"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 16, 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = "rgba(" + ORANGE + "," + (0.15 + close * 0.6) + ")";
        ctx.beginPath(); ctx.arc(x, y, 4 + close * 10, 0, Math.PI * 2); ctx.fill();
      }
      ctx.restore();

      // caption under the bust
      ctx.save();
      ctx.textAlign = "center"; ctx.shadowColor = "rgba(" + rgb + ",0.9)"; ctx.shadowBlur = 12;
      ctx.fillStyle = "rgba(" + rgb + ",0.95)"; ctx.font = "700 " + Math.max(13, S * 0.085) + "px ui-monospace,SFMono-Regular,Menlo,monospace";
      ctx.fillText("J.A.R.V.I.S.", cx, Math.min(H - 36, cy + S * 1.2));
      ctx.restore();
    }

    return {
      draw,
      pinch() { pinchT = 0; shock.push({ t: 0, rgb: ORANGE }); },
      replayIntro() { introT = 0; },
      get introDone() { return introT > 3.2; },
    };
  }

  window.ApexAvatar = { create, profile };
})();

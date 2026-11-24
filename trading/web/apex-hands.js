// APEX hand tracking: pinch your thumb and index finger in front of the webcam to wake JARVIS.
// Opt-in only (the camera starts when you press the button). Everything runs in this browser with MediaPipe
// Hands served from our own site; no video or picture ever leaves the computer.
// Sends window events: "apex:hand" {x, y, closeness} (x mirrored so it moves like a mirror), "apex:hand-lost",
// "apex:pinch", "apex:camera" {on, error}.
const BASE = "/vendor/mediapipe/";
let landmarker = null, video = null, stream = null, running = false, lastVideoTime = -1;
let pinched = false, lastPinch = 0, seen = false;

const emit = (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail }));
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, (a.z || 0) - (b.z || 0));

// Pinch = thumb tip (4) close to index tip (8), measured against the hand's own size (wrist 0 to middle knuckle 9)
// so it works near or far from the camera. Two thresholds stop it flickering.
export function pinchRatio(hand) { return dist(hand[4], hand[8]) / Math.max(1e-6, dist(hand[0], hand[9])); }
export const PINCH_ON = 0.28, PINCH_OFF = 0.45, PINCH_GAP_MS = 1200;

async function load() {
  const vision = await import(BASE + "vision_bundle.mjs");
  const files = { wasmLoaderPath: BASE + "vision_wasm_internal.js", wasmBinaryPath: BASE + "vision_wasm_internal.wasm" };
  const make = (delegate) => vision.HandLandmarker.createFromOptions(files, {
    baseOptions: { modelAssetPath: BASE + "hand_landmarker.task", delegate },
    runningMode: "VIDEO", numHands: 1,
    minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.6, minTrackingConfidence: 0.5,
  });
  try { return await make("GPU"); } catch (e) { return await make("CPU"); }
}

function loop() {
  if (!running) return;
  if (video.readyState >= 2 && video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    const res = landmarker.detectForVideo(video, performance.now());
    const hand = res && res.landmarks && res.landmarks[0];
    if (hand) {
      seen = true;
      const r = pinchRatio(hand);
      const tip = { x: 1 - (hand[4].x + hand[8].x) / 2, y: (hand[4].y + hand[8].y) / 2 };
      emit("apex:hand", { x: tip.x, y: tip.y, closeness: 1 - (r - PINCH_ON) / (PINCH_OFF + 0.4 - PINCH_ON) });
      const now = performance.now();
      if (!pinched && r < PINCH_ON) {
        pinched = true;
        if (now - lastPinch > PINCH_GAP_MS) { lastPinch = now; emit("apex:pinch", {}); }
      } else if (pinched && r > PINCH_OFF) pinched = false;
    } else if (seen) { seen = false; pinched = false; emit("apex:hand-lost", {}); }
  }
  requestAnimationFrame(loop);
}

export async function start(previewHost) {
  if (running) return true;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("This browser can't use a camera.");
  stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
  video = document.createElement("video");
  video.className = "apex-cam"; video.muted = true; video.playsInline = true; video.autoplay = true;
  video.srcObject = stream;
  if (previewHost) previewHost.appendChild(video);
  await video.play();
  if (!landmarker) landmarker = await load();
  running = true; lastVideoTime = -1;
  emit("apex:camera", { on: true });
  requestAnimationFrame(loop);
  return true;
}

export function stop() {
  running = false;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (video) video.remove();
  stream = null; video = null; seen = false; pinched = false;
  emit("apex:hand-lost", {});
  emit("apex:camera", { on: false });
}

window.ApexHands = { start, stop, pinchRatio };

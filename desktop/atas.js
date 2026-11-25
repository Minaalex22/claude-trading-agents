// One-click Connect ATAS for the desktop app: does what the Terminal line on One account does, with no Terminal.
// It asks the signed-in app for a fresh install code, downloads the same installer script the line would run
// (install.sh on a Mac, install.ps1 on Windows) and runs it here, showing its output in a small window.
// It never asks for the ATAS password and never places orders.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ANSI = /\x1b\[[0-9;]*m/g;

function cleanLine(s) {
  return String(s).replace(ANSI, "").replace(/\r/g, "");
}

// Installer for this computer, or null where ATAS can't run (Linux).
function installerFor(platform) {
  if (platform === "darwin") return { kind: "sh", file: "install.sh" };
  if (platform === "win32") return { kind: "ps1", file: "install.ps1" };
  return null;
}

// Apps opened from the Dock get a bare PATH; add the usual places so curl, dotnet etc. are found.
function shellEnv(platform, base) {
  const env = { ...base };
  if (platform === "darwin") {
    const extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"];
    const have = (env.PATH || "").split(":").filter(Boolean);
    env.PATH = [...have, ...extra.filter((p) => !have.includes(p))].join(":");
  }
  env.HOME = env.HOME || os.homedir();
  return env;
}

function commandFor(platform, scriptPath) {
  if (platform === "win32") {
    return { cmd: "powershell.exe", args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", scriptPath] };
  }
  return { cmd: "/bin/bash", args: [scriptPath] };
}

// fetchFn(url) -> Response, using the app's signed-in session. log(line) shows progress.
async function connect({ server, platform, fetchFn, log, env = process.env }) {
  const inst = installerFor(platform);
  if (!inst) throw new Error("ATAS runs on Mac or Windows. Open this app on the computer that has ATAS.");

  log("Asking the trading system for a fresh install code...");
  const r = await fetchFn(`${server}/api/atas/install`);
  if (r.status === 401 || r.status === 403 || /\/login/.test(r.url || "")) {
    const e = new Error("Sign in to the app first, then press Connect ATAS again.");
    e.signIn = true;
    throw e;
  }
  let j = {};
  try { j = await r.json(); } catch { /* not JSON: handled below */ }
  if (!r.ok || !j.code) throw new Error(j.error || `The trading system said no (${r.status}). Try again in a minute.`);

  log("Downloading the ATAS add-on installer...");
  const s = await fetchFn(`${server}/atas/${inst.file}?c=${encodeURIComponent(j.code)}`);
  const body = await s.text();
  if (!s.ok || body.length < 200) throw new Error(cleanLine(body).replace(/^echo '|'\s*$/g, "").trim() || "Couldn't download the installer.");

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jarvisfeed-"));
  const script = path.join(dir, inst.file);
  fs.writeFileSync(script, body, { mode: 0o700 });

  const { cmd, args } = commandFor(platform, script);
  log("Installing (the first time can take a few minutes while it gets the .NET builder)...");
  const code = await new Promise((resolve) => {
    const child = spawn(cmd, args, { env: shellEnv(platform, env), cwd: os.homedir(), windowsHide: true });
    let tail = "";
    const pump = (chunk) => {
      tail += chunk.toString();
      const parts = tail.split("\n");
      tail = parts.pop();
      for (const p of parts) { const l = cleanLine(p); if (l.trim()) log(l); }
    };
    child.stdout.on("data", pump);
    child.stderr.on("data", pump);
    child.on("error", (e) => { log(`Couldn't start the installer: ${e.message}`); resolve(127); });
    child.on("close", (c) => { if (tail.trim()) log(cleanLine(tail)); resolve(c); });
  });
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* temp folder, not important */ }
  if (code !== 0) throw new Error("The install stopped. Press Copy log and send it in the chat; it gets fixed.");
  return { ok: true };
}

module.exports = { connect, installerFor, shellEnv, commandFor, cleanLine };

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const atas = require("../atas");

const res = (status, body, url = "") => ({ status, ok: status >= 200 && status < 300, url,
  json: async () => JSON.parse(body), text: async () => body });

test("installer per computer", () => {
  assert.strictEqual(atas.installerFor("darwin").file, "install.sh");
  assert.strictEqual(atas.installerFor("win32").file, "install.ps1");
  assert.strictEqual(atas.installerFor("linux"), null);
  assert.deepStrictEqual(atas.commandFor("darwin", "/x/install.sh"), { cmd: "/bin/bash", args: ["/x/install.sh"] });
  assert.strictEqual(atas.commandFor("win32", "C:\\x.ps1").cmd, "powershell.exe");
});

test("Dock-launched app still finds curl and homebrew", () => {
  const env = atas.shellEnv("darwin", { PATH: "/usr/bin" });
  for (const p of ["/opt/homebrew/bin", "/usr/local/bin", "/bin"]) assert.ok(env.PATH.split(":").includes(p));
  assert.strictEqual(env.PATH.split(":").filter((p) => p === "/usr/bin").length, 1);
});

test("colour codes are stripped from installer output", () => {
  assert.strictEqual(atas.cleanLine("\x1b[38;5;208m==>\x1b[0m Found ATAS X\r"), "==> Found ATAS X");
});

test("not signed in asks to sign in", async () => {
  await assert.rejects(atas.connect({ server: "https://x", platform: "darwin", log: () => {},
    fetchFn: async () => res(401, "{}") }), (e) => e.signIn === true);
});

test("expired or refused code shows the server's reason", async () => {
  await assert.rejects(atas.connect({ server: "https://x", platform: "darwin", log: () => {},
    fetchFn: async () => res(409, JSON.stringify({ error: "the ATAS feed is off" })) }), /feed is off/);
});

test("downloads with the fresh code and runs the installer", { skip: process.platform === "win32" }, async () => {
  const seen = []; const lines = [];
  const script = "#!/bin/bash\n" + "# padding\n".repeat(30) + "printf '\\033[38;5;208m==>\\033[0m Installed\\n'\n";
  await atas.connect({ server: "https://x", platform: "darwin", log: (l) => lines.push(l),
    fetchFn: async (url) => { seen.push(url); return url.endsWith("/api/atas/install") ? res(200, JSON.stringify({ code: "ab c" })) : res(200, script); } });
  assert.deepStrictEqual(seen, ["https://x/api/atas/install", "https://x/atas/install.sh?c=ab%20c"]);
  assert.ok(lines.includes("==> Installed"));
});

test("a failing installer is reported", { skip: process.platform === "win32" }, async () => {
  const script = "#!/bin/bash\n" + "# padding\n".repeat(30) + "echo 'Stopped: build failed' >&2; exit 1\n";
  const lines = [];
  await assert.rejects(atas.connect({ server: "https://x", platform: "darwin", log: (l) => lines.push(l),
    fetchFn: async (url) => url.endsWith("/install") ? res(200, '{"code":"c"}') : res(200, script) }), /Copy log/);
  assert.ok(lines.some((l) => l.includes("build failed")));
});

test("the app ships the ATAS files", () => {
  const files = require("../package.json").build.files;
  for (const f of ["atas.js", "atas.html", "atas-preload.js"]) {
    assert.ok(files.includes(f), f);
    assert.ok(fs.existsSync(path.join(__dirname, "..", f)), f);
  }
});

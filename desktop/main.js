// JARVIS Trading desktop app: one window that opens straight onto the trading pages. No browser needed.
// Sign in once; the login is kept between launches (same as "remember me").
const { app, BrowserWindow, Menu, shell, ipcMain, nativeTheme, clipboard } = require("electron");
const fs = require("fs");
const path = require("path");
const { DEFAULT_SERVER, PAGES, isInternal, normalizeServer } = require("./pages");
const atas = require("./atas");

const RETRY_FOR_MS = 120000; // Render's free plan can take about a minute to wake up.
let win = null;
let atasWin = null;
let firstLoadStarted = 0;

function settingsFile() {
  return path.join(app.getPath("userData"), "settings.json");
}
function readSettings() {
  try { return JSON.parse(fs.readFileSync(settingsFile(), "utf8")); } catch { return {}; }
}
function writeSettings(s) {
  try { fs.writeFileSync(settingsFile(), JSON.stringify(s, null, 2)); } catch { /* not fatal */ }
}
function server() {
  return normalizeServer(process.env.JARVIS_SERVER || readSettings().server || DEFAULT_SERVER);
}

function showLoading(state) {
  win.loadFile(path.join(__dirname, "loading.html"), { query: state ? { state } : {} });
}

function openApp(pagePath) {
  firstLoadStarted = firstLoadStarted || Date.now();
  win.loadURL(server() + (pagePath || readSettings().lastPage || "/news"));
}

function retryLater() {
  if (Date.now() - firstLoadStarted > RETRY_FOR_MS) {
    firstLoadStarted = 0;
    return showLoading("error");
  }
  showLoading("waking");
  setTimeout(() => openApp(), 4000);
}

function go(pagePath) {
  if (win) win.loadURL(server() + pagePath);
}

// Connect ATAS with one click: runs the same installer as the Terminal line, inside the app.
function connectAtas() {
  if (atasWin && !atasWin.isDestroyed()) { atasWin.focus(); return; }
  atasWin = new BrowserWindow({
    width: 620, height: 520, minWidth: 420, minHeight: 380, parent: win || undefined,
    title: "Connect ATAS", backgroundColor: "#000000",
    webPreferences: { preload: path.join(__dirname, "atas-preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  atasWin.setMenuBarVisibility(false);
  atasWin.on("closed", () => { atasWin = null; });
  const send = (ch, v) => { if (atasWin && !atasWin.isDestroyed()) atasWin.webContents.send(ch, v); };
  atasWin.webContents.once("did-finish-load", async () => {
    const ses = (win || atasWin).webContents.session;
    try {
      await atas.connect({
        server: server(),
        platform: process.platform,
        fetchFn: async (url) => {
          const r = await ses.fetch(url, { credentials: "include", cache: "no-store" });
          if (r.status !== 401 || !win || !isInternal(win.webContents.getURL(), server())) return r;
          // Fallback: ask from inside the signed-in page itself, which always carries the login.
          const out = await win.webContents.executeJavaScript(
            `fetch(${JSON.stringify(url)}, { cache: "no-store" }).then(async (x) => ({ status: x.status, url: x.url, text: await x.text() }))`);
          return new Response(out.text, { status: out.status });
        },
        log: (line) => send("atas-log", line),
      });
      send("atas-done", { ok: true });
      if (win) go("/account");
    } catch (e) {
      send("atas-done", { ok: false, error: e.message || String(e) });
      if (e.signIn && win) go("/login");
    }
  });
  atasWin.loadFile(path.join(__dirname, "atas.html"));
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const pagesMenu = PAGES.map((p) => ({
    label: p.label,
    accelerator: p.key ? `CmdOrCtrl+${p.key}` : undefined,
    click: () => go(p.path),
  }));
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        { label: "Connect ATAS", click: () => connectAtas() },
        { type: "separator" },
        { label: "Sign out", click: () => go("/logout") },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    { role: "editMenu" },
    { label: "Pages", submenu: pagesMenu },
    {
      label: "View",
      submenu: [
        { label: "Back", accelerator: "CmdOrCtrl+[", click: () => win && win.webContents.navigationHistory.goBack() },
        { label: "Forward", accelerator: "CmdOrCtrl+]", click: () => win && win.webContents.navigationHistory.goForward() },
        { role: "reload" },
        { type: "separator" },
        { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  const s = readSettings();
  const bounds = s.bounds || { width: 1440, height: 900 };
  nativeTheme.themeSource = "dark";
  win = new BrowserWindow({
    ...bounds,
    minWidth: 380,
    minHeight: 500,
    title: "JARVIS Trading",
    backgroundColor: "#000000",
    icon: path.join(__dirname, "build", "icon.png"),
    autoHideMenuBar: process.platform !== "darwin",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  if (s.maximized) win.maximize();

  // Links to other websites open in the normal browser; the trading pages stay in this window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isInternal(url, server())) { win.loadURL(url); } else if (/^https?:/.test(url)) { shell.openExternal(url); }
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (url.startsWith("file:")) return;
    if (!isInternal(url, server())) { e.preventDefault(); if (/^https?:/.test(url)) shell.openExternal(url); }
  });

  // Server asleep or offline: show the loading screen and keep trying for up to two minutes.
  win.webContents.on("did-fail-load", (_e, code, _desc, url, isMain) => {
    if (isMain && code !== -3 && !String(url).startsWith("file:")) retryLater();
  });
  win.webContents.on("did-navigate", (_e, url, status) => {
    if (url.startsWith("file:")) return;
    if (status >= 500) return retryLater();
    firstLoadStarted = 0;
    const p = new URL(url).pathname;
    if (!["/login", "/logout"].includes(p)) writeSettings({ ...readSettings(), lastPage: p });
  });

  const saveBounds = () => {
    if (!win || win.isDestroyed()) return;
    writeSettings({ ...readSettings(), maximized: win.isMaximized(), bounds: win.isMaximized() ? readSettings().bounds : win.getBounds() });
  };
  win.on("resize", saveBounds);
  win.on("move", saveBounds);
  win.on("closed", () => { win = null; });

  showLoading();
  openApp();
}

// The One account page asks for this when it runs inside the desktop app (only our own pages may).
ipcMain.handle("connect-atas", (e) => {
  const from = (e.senderFrame && e.senderFrame.url) || "";
  if (!isInternal(from, server())) return { ok: false };
  connectAtas();
  return { ok: true };
});
ipcMain.on("atas-copy", (_e, text) => clipboard.writeText(String(text || "")));

ipcMain.on("retry", () => { firstLoadStarted = 0; showLoading(); openApp(); });

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(() => {
    buildMenu();
    createWindow();
    app.on("activate", () => { if (!win) createWindow(); });
  });
  app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
}

// JARVIS Trading desktop app: one window that opens straight onto the trading pages. No browser needed.
// Sign in once; the login is kept between launches (same as "remember me").
const { app, BrowserWindow, Menu, shell, ipcMain, nativeTheme } = require("electron");
const fs = require("fs");
const path = require("path");
const { DEFAULT_SERVER, PAGES, isInternal, normalizeServer } = require("./pages");

const RETRY_FOR_MS = 120000; // Render's free plan can take about a minute to wake up.
let win = null;
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

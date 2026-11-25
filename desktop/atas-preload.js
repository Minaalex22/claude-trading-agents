// Bridge for the Connect ATAS window only (a local page inside the app).
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("atasWin", {
  onLog: (fn) => ipcRenderer.on("atas-log", (_e, line) => fn(line)),
  onDone: (fn) => ipcRenderer.on("atas-done", (_e, res) => fn(res)),
  copy: (text) => ipcRenderer.send("atas-copy", text),
  close: () => window.close(),
});

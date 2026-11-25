// Small bridge: retry for the loading screen, and one-click Connect ATAS for the One account page.
// The main process only accepts connectAtas from the app's own server.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  retry: () => ipcRenderer.send("retry"),
  connectAtas: () => ipcRenderer.invoke("connect-atas"),
});

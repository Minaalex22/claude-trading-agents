// Small bridge for the loading screen only. The trading pages themselves get nothing extra.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  retry: () => ipcRenderer.send("retry"),
});

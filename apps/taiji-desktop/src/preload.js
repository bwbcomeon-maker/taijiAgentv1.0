const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("taijiDesktop", {
  pickDirectory: () => ipcRenderer.invoke("taiji:pick-directory"),
  readClipboardText: () => ipcRenderer.invoke("taiji:read-clipboard-text")
});

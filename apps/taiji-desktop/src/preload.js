const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("taijiDesktop", {
  pickDirectory: () => ipcRenderer.invoke("taiji:pick-directory")
});

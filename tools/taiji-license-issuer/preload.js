"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("taijiLicenseIssuer", {
  getStatus: () => ipcRenderer.invoke("issuer:get-status"),
  initializeKey: () => ipcRenderer.invoke("issuer:initialize-key"),
  chooseOutput: () => ipcRenderer.invoke("issuer:choose-output"),
  generate: (form) => ipcRenderer.invoke("issuer:generate", form),
});

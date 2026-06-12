"use strict";

const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const fs = require("fs");
const os = require("os");
const path = require("path");
const core = require("./issuer-core");

let mainWindow = null;

function safeError(err) {
  return err && err.message ? err.message : String(err);
}

function defaultOutputPath() {
  return path.join(app.getPath("desktop") || os.homedir(), "license.jwt");
}

function defaultBatchOutputPath() {
  return path.join(app.getPath("desktop") || os.homedir(), "taiji-licenses.zip");
}

function privateKeyStatus() {
  const privateKeyPath = core.resolvePrivateKeyPath();
  return {
    privateKeyPath,
    publicKeyPath: core.resolvePublicKeyPath({ privateKeyPath }),
    privateKeyInstalled: fs.existsSync(privateKeyPath),
    privateKeyFromEnv: Boolean(process.env[core.PRIVATE_KEY_ENV]),
    recordPath: core.defaultRecordPath(),
    suggestedOutputPath: defaultOutputPath(),
    suggestedBatchOutputPath: defaultBatchOutputPath(),
  };
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1040,
    height: 760,
    minWidth: 920,
    minHeight: 640,
    title: "太极 License 签发工具",
    backgroundColor: "#f3f6f8",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  mainWindow.loadFile(path.join(__dirname, "index.html"));
  if (process.env.TAIJI_LICENSE_ISSUER_SMOKE === "1") {
    mainWindow.webContents.once("did-finish-load", () => {
      setTimeout(() => app.quit(), 250);
    });
  }
}

ipcMain.handle("issuer:get-status", () => {
  return privateKeyStatus();
});

ipcMain.handle("issuer:initialize-key", async () => {
  try {
    const privateKeyPath = core.resolvePrivateKeyPath();
    if (fs.existsSync(privateKeyPath)) {
      return {
        ok: true,
        existing: true,
        privateKeyPath,
        publicKeyPath: core.resolvePublicKeyPath({ privateKeyPath }),
      };
    }
    const result = core.initializeSigningKeyPair({ privateKeyPath });
    return {
      ok: true,
      existing: false,
      privateKeyPath: result.privateKeyPath,
      publicKeyPath: result.publicKeyPath,
      publicKeyPem: result.publicKeyPem,
    };
  } catch (err) {
    return { ok: false, error: safeError(err) };
  }
});

ipcMain.handle("issuer:choose-output", async () => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "导出授权文件",
    defaultPath: defaultOutputPath(),
    filters: [{ name: "Taiji license", extensions: ["jwt"] }],
  });
  if (result.canceled || !result.filePath) {
    return { canceled: true };
  }
  return { canceled: false, filePath: result.filePath };
});

ipcMain.handle("issuer:choose-machine-request", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择本机机器码文件",
    properties: ["openFile"],
    filters: [{ name: "Taiji machine request", extensions: ["json"] }],
  });
  if (result.canceled || !result.filePaths || !result.filePaths[0]) {
    return { canceled: true };
  }
  try {
    const request = core.readMachineRequestFile(result.filePaths[0]);
    return { canceled: false, filePath: result.filePaths[0], requests: [request] };
  } catch (err) {
    return { canceled: false, ok: false, error: safeError(err), filePath: result.filePaths[0], requests: [] };
  }
});

ipcMain.handle("issuer:choose-machine-request-dir", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择机器码文件目录",
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths || !result.filePaths[0]) {
    return { canceled: true };
  }
  try {
    const requests = core.readMachineRequestDirectory(result.filePaths[0]);
    return { canceled: false, dirPath: result.filePaths[0], requests };
  } catch (err) {
    return { canceled: false, ok: false, error: safeError(err), dirPath: result.filePaths[0], requests: [] };
  }
});

ipcMain.handle("issuer:generate", async (_event, form) => {
  try {
    const privateKeyPath = core.resolvePrivateKeyPath();
    if (!fs.existsSync(privateKeyPath)) {
      return { ok: false, code: "private_key_missing", error: `发证私钥未安装：${privateKeyPath}` };
    }
    const machineRequests = Array.isArray(form.machineRequests) ? form.machineRequests : [];
    const common = {
      customer: form.customer,
      days: Number(form.days),
      features: form.features,
      licenseId: form.licenseId,
      notBefore: form.notBefore,
      maxVersion: form.maxVersion,
      outputPath: form.outputPath,
      privateKeyPath,
    };
    const result = machineRequests.length > 1
      ? core.issueBatchZip({ ...common, machineRequests })
      : core.issueAndWriteLicense({ ...common, machineRequest: machineRequests[0] });
    return {
      ok: true,
      batch: machineRequests.length > 1,
      outputPath: result.outputPath,
      publicKeyPath: core.resolvePublicKeyPath({ privateKeyPath }),
      recordPath: result.recordPath,
      payload: result.payload || null,
      tokenHash: result.tokenHash || null,
      files: result.files || [],
    };
  } catch (err) {
    return { ok: false, error: safeError(err) };
  }
});

app.whenReady().then(createWindow);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

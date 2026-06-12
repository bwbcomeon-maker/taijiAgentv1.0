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

function privateKeyStatus() {
  const privateKeyPath = core.resolvePrivateKeyPath();
  return {
    privateKeyPath,
    privateKeyInstalled: fs.existsSync(privateKeyPath),
    privateKeyFromEnv: Boolean(process.env[core.PRIVATE_KEY_ENV]),
    recordPath: core.defaultRecordPath(),
    suggestedOutputPath: defaultOutputPath(),
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

ipcMain.handle("issuer:generate", async (_event, form) => {
  try {
    const privateKeyPath = core.resolvePrivateKeyPath();
    if (!fs.existsSync(privateKeyPath)) {
      return { ok: false, error: `发证私钥未安装：${privateKeyPath}` };
    }
    const result = core.issueAndWriteLicense({
      customer: form.customer,
      days: Number(form.days),
      features: form.features,
      licenseId: form.licenseId,
      notBefore: form.notBefore,
      maxVersion: form.maxVersion,
      outputPath: form.outputPath,
      privateKeyPath,
    });
    return {
      ok: true,
      outputPath: result.outputPath,
      recordPath: result.recordPath,
      payload: result.payload,
      tokenHash: result.tokenHash,
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

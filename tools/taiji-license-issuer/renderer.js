"use strict";

const form = document.getElementById("licenseForm");
const keyStatus = document.getElementById("keyStatus");
const keyPath = document.getElementById("keyPath");
const publicKeyPath = document.getElementById("publicKeyPath");
const resultBox = document.getElementById("resultBox");
const outputPath = document.getElementById("outputPath");
const chooseOutput = document.getElementById("chooseOutput");
const machineRequestPath = document.getElementById("machineRequestPath");
const chooseMachineRequest = document.getElementById("chooseMachineRequest");
const chooseMachineRequestDir = document.getElementById("chooseMachineRequestDir");
const initializeKey = document.getElementById("initializeKey");
const resetBtn = document.getElementById("resetBtn");
const generateBtn = document.getElementById("generateBtn");
const productStatus = document.getElementById("productStatus");
const summary = document.getElementById("summary");
const machineListSection = document.getElementById("machineListSection");
const machineList = document.getElementById("machineList");
let machineRequests = [];
let statusCache = {};
let outputPathAuto = true;

const PRODUCT_PENDING_TEXT = "待识别";
const PRODUCT_FAILED_TEXT = "识别失败";

function value(id) {
  return document.getElementById(id).value.trim();
}

function setResult(text, mode) {
  resultBox.textContent = text;
  resultBox.className = `result-box ${mode || "muted"}`;
}

function isoUtc(date) {
  return date.toISOString().replace(".000Z", "Z");
}

function sanitizeFilePart(value, fallback) {
  const text = String(value || "")
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
  return (text || fallback).slice(0, 72);
}

function compactTimestamp(date) {
  return isoUtc(date).replace(/[-:]/g, "").replace("T", "-");
}

function parseNotBefore(input) {
  const text = input.trim();
  if (!text) return new Date();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return new Date(`${text}T00:00:00Z`);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) return new Date(`${text}:00Z`);
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) return new Date(`${text}Z`);
  return new Date(text);
}

// 单台机器的产品展示：国网与显式声明的太极显示“已自动识别”，
// 缺少产品声明的旧太极机器码显示“兼容旧版机器码”。
function productDisplay(request) {
  const product = request && request.productRecognition;
  if (!product) return "-";
  if (product.productId === "taiji_agent" && /旧版/.test(product.basis || "")) {
    return `${product.productName} · 兼容旧版机器码`;
  }
  return `${product.productName} · 已自动识别`;
}

function productCounts(requests) {
  const counts = new Map();
  for (const request of requests) {
    const name = (request.productRecognition && request.productRecognition.productName) || "未知产品";
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return counts;
}

function productSummaryText() {
  if (!machineRequests.length) return PRODUCT_PENDING_TEXT;
  const counts = productCounts(machineRequests);
  if (counts.size === 1) {
    const [[name, count]] = [...counts];
    return count > 1 ? `${name} · 已自动识别 · ${count} 台` : productDisplay(machineRequests[0]);
  }
  return `混合 · ${[...counts].map(([name, count]) => `${name}×${count}`).join(" + ")}`;
}

function renderProductStatus() {
  if (!machineRequests.length) {
    productStatus.value = PRODUCT_PENDING_TEXT;
    generateBtn.disabled = true;
    machineListSection.hidden = true;
    machineList.replaceChildren();
    return;
  }
  productStatus.value = productSummaryText();
  // 混合批量的产品汇总可能长于输入框宽度：title 提示保证悬停可见完整文本
  productStatus.title = productSummaryText();
  generateBtn.disabled = false;
  machineList.replaceChildren();
  for (const request of machineRequests) {
    const item = document.createElement("li");
    const name = (request.productRecognition && request.productRecognition.productName) || "未知产品";
    const label = request.machineLabel || request.hostname || "";
    item.textContent = `${name} · ${request.machineCodeShort || "-"}${label ? ` · ${label}` : ""}`;
    machineList.append(item);
  }
  // 单台机器的产品与短码已在表单和摘要中展示，列表只在批量时出现。
  machineListSection.hidden = machineRequests.length < 2;
}

function updateSummary() {
  const days = Number(value("days") || "0");
  const nbf = parseNotBefore(value("notBefore"));
  const exp = Number.isFinite(days) && days > 0 && !Number.isNaN(nbf.getTime())
    ? new Date(nbf.getTime() + days * 86400 * 1000)
    : null;
  const rows = [
    ["客户", value("customer") || "-"],
    ["起始", Number.isNaN(nbf.getTime()) ? "时间格式无效" : isoUtc(nbf)],
    ["到期", exp ? isoUtc(exp) : "-"],
    ["产品", machineRequests.length ? productSummaryText() : PRODUCT_PENDING_TEXT],
    ["机器", machineRequests.length ? `${machineRequests.length} 台 · ${machineRequests[0].machineCodeShort || "-"}` : "-"],
  ];
  summary.replaceChildren();
  for (const [label, text] of rows) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = text;
    row.append(term, detail);
    summary.append(row);
  }
  refreshSuggestedOutputPath();
}

function outputDirectory() {
  const current = outputPath.value || statusCache.suggestedOutputPath || "";
  const normalized = current.replace(/\\/g, "/");
  const index = normalized.lastIndexOf("/");
  return index >= 0 ? current.slice(0, index) : "";
}

function joinPath(dir, name) {
  return dir ? `${dir.replace(/[\\/]+$/, "")}/${name}` : name;
}

function suggestedOutputName() {
  const customer = sanitizeFilePart(value("customer"), "customer");
  const now = new Date();
  if (machineRequests.length > 1) {
    return `taiji-licenses-${customer}-${machineRequests.length}台-${compactTimestamp(now)}.zip`;
  }
  const machine = machineRequests[0];
  if (!machine) {
    return "";
  }
  const days = Number(value("days") || "0");
  const nbf = parseNotBefore(value("notBefore"));
  if (!Number.isFinite(days) || days <= 0 || Number.isNaN(nbf.getTime())) {
    return "";
  }
  const exp = new Date(nbf.getTime() + days * 86400 * 1000);
  return [
    "taiji-license",
    customer,
    sanitizeFilePart(machine.machineLabel || machine.hostname, "terminal"),
    sanitizeFilePart(machine.machineCodeShort, "machine"),
    compactTimestamp(nbf),
    compactTimestamp(exp),
  ].join("-") + ".jwt";
}

function refreshSuggestedOutputPath() {
  if (!outputPathAuto) return;
  const name = suggestedOutputName();
  if (!name) return;
  outputPath.value = joinPath(outputDirectory(), name);
}

function readForm() {
  return {
    customer: value("customer"),
    days: value("days"),
    licenseId: value("licenseId"),
    notBefore: value("notBefore"),
    maxVersion: value("maxVersion"),
    outputPath: value("outputPath"),
    machineRequests,
  };
}

async function loadStatus() {
  const status = await window.taijiLicenseIssuer.getStatus();
  statusCache = status || {};
  if (!outputPath.value) {
    outputPath.value = status.suggestedOutputPath || "";
    outputPathAuto = true;
  }
  keyPath.textContent = status.privateKeyPath || "-";
  publicKeyPath.textContent = status.publicKeyPath ? `公钥：${status.publicKeyPath}` : "-";
  if (status.privateKeyInstalled) {
    keyStatus.textContent = status.privateKeyFromEnv ? "私钥已安装 (环境变量)" : "私钥已安装";
    keyStatus.className = "status-pill ok";
    initializeKey.disabled = true;
  } else {
    keyStatus.textContent = "发证私钥未安装";
    keyStatus.className = "status-pill danger";
    initializeKey.disabled = false;
    setResult("缺少签发私钥。点击“初始化签发密钥”后可导出授权文件。", "danger");
  }
  updateSummary();
}

async function initializeSigningKey() {
  setResult("正在初始化签发密钥...", "muted");
  const response = await window.taijiLicenseIssuer.initializeKey();
  if (!response.ok) {
    setResult(response.error || "初始化签发密钥失败", "danger");
    return false;
  }
  await loadStatus();
  setResult(
    [
      response.existing ? "签发私钥已存在。" : "已初始化签发密钥。",
      `私钥：${response.privateKeyPath}`,
      `公钥：${response.publicKeyPath}`,
      "生成的 license 需要使用这份公钥进行产品校验。",
    ].join("\n"),
    "ok",
  );
  return true;
}

chooseOutput.addEventListener("click", async () => {
  const selected = await window.taijiLicenseIssuer.chooseOutput();
  if (!selected.canceled && selected.filePath) {
    outputPath.value = selected.filePath;
    outputPathAuto = false;
  }
});

outputPath.addEventListener("input", () => {
  outputPathAuto = false;
});

function applyMachineSelection(selected) {
  // 取消文件选择：保留此前有效选择，不做任何变更。
  if (!selected || selected.canceled) return;
  if (selected.ok === false) {
    machineRequests = [];
    machineRequestPath.value = selected.filePath || selected.dirPath || "";
    renderProductStatus();
    productStatus.value = PRODUCT_FAILED_TEXT;
    setResult(selected.error || "机器码文件读取失败", "danger");
    updateSummary();
    return;
  }
  machineRequests = selected.requests || [];
  machineRequestPath.value = selected.filePath || selected.dirPath || "";
  const currentName = (outputPath.value || "").replace(/\\/g, "/").split("/").pop().toLowerCase();
  if (!outputPath.value || currentName === "license.jwt" || currentName === "taiji-licenses.zip" || currentName === "licenses.zip") {
    outputPathAuto = true;
  }
  refreshSuggestedOutputPath();
  renderProductStatus();
  setResult(
    machineRequests.length > 1
      ? `已导入 ${machineRequests.length} 台机器码（${productSummaryText()}），批量导出将生成 zip。`
      : `已导入机器码：${machineRequests[0] ? machineRequests[0].machineCodeShort : "-"}（${productDisplay(machineRequests[0])}）`,
    "muted",
  );
  updateSummary();
}

chooseMachineRequest.addEventListener("click", async () => {
  const selected = await window.taijiLicenseIssuer.chooseMachineRequest();
  applyMachineSelection(selected);
});

chooseMachineRequestDir.addEventListener("click", async () => {
  const selected = await window.taijiLicenseIssuer.chooseMachineRequestDir();
  applyMachineSelection(selected);
});

initializeKey.addEventListener("click", () => {
  initializeSigningKey().catch((err) => setResult(err.message || String(err), "danger"));
});

resetBtn.addEventListener("click", () => {
  form.reset();
  machineRequests = [];
  machineRequestPath.value = "";
  outputPathAuto = true;
  document.getElementById("days").value = "30";
  renderProductStatus();
  setResult("等待生成", "muted");
  loadStatus().catch((err) => setResult(err.message || String(err), "danger"));
});

form.addEventListener("input", updateSummary);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!machineRequests.length) {
    renderProductStatus();
    setResult("请先选择机器码文件，识别授权产品后再导出。", "danger");
    return;
  }
  setResult("正在生成...", "muted");
  let response = await window.taijiLicenseIssuer.generate(readForm());
  if (!response.ok && response.code === "private_key_missing") {
    const confirmed = window.confirm("缺少签发私钥。是否现在初始化签发密钥并继续导出？");
    if (confirmed) {
      const initialized = await initializeSigningKey();
      if (initialized) {
        setResult("正在生成...", "muted");
        response = await window.taijiLicenseIssuer.generate(readForm());
      }
    }
  }
  if (!response.ok) {
    setResult(response.error || "生成失败", "danger");
    return;
  }
  const exportLine = response.batch
    ? `已导出批量包：${response.outputPath}`
    : `已导出：${response.outputPath}`;
  const productLine = response.batch
    ? `授权产品：${[...productCounts((response.files || []).map((file) => ({ product: { productName: file.product_name } })))].map(([name, count]) => `${name}×${count}`).join(" + ")}`
    : `授权产品：${response.product ? `${response.product.productName}（${response.product.features.join(",")}）` : "-"}`;
  const expiryLine = response.payload
    ? `机器短码：${response.payload.machine_code_short} · 到期时间：${response.payload.expires_at}`
    : `授权数量：${response.files.length} 台`;
  setResult(
    [
      exportLine,
      productLine,
      expiryLine,
      `公钥：${response.publicKeyPath}`,
      `签发记录：${response.recordPath}`,
      response.tokenHash ? `摘要：${response.tokenHash}` : `文件：${response.files.map((item) => item.name).join(", ")}`,
    ].join("\n"),
    "ok",
  );
});

renderProductStatus();
loadStatus().catch((err) => {
  keyStatus.textContent = "状态读取失败";
  keyStatus.className = "status-pill danger";
  setResult(err.message || String(err), "danger");
});

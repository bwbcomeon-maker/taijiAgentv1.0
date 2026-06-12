"use strict";

const form = document.getElementById("licenseForm");
const keyStatus = document.getElementById("keyStatus");
const keyPath = document.getElementById("keyPath");
const resultBox = document.getElementById("resultBox");
const outputPath = document.getElementById("outputPath");
const chooseOutput = document.getElementById("chooseOutput");
const resetBtn = document.getElementById("resetBtn");
const summary = document.getElementById("summary");

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

function parseNotBefore(input) {
  const text = input.trim();
  if (!text) return new Date();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return new Date(`${text}T00:00:00Z`);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) return new Date(`${text}:00Z`);
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) return new Date(`${text}Z`);
  return new Date(text);
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
    ["功能", value("features") || "-"],
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
}

function readForm() {
  return {
    customer: value("customer"),
    days: value("days"),
    features: value("features"),
    licenseId: value("licenseId"),
    notBefore: value("notBefore"),
    maxVersion: value("maxVersion"),
    outputPath: value("outputPath"),
  };
}

async function loadStatus() {
  const status = await window.taijiLicenseIssuer.getStatus();
  outputPath.value = status.suggestedOutputPath || "";
  keyPath.textContent = status.privateKeyPath || "-";
  if (status.privateKeyInstalled) {
    keyStatus.textContent = status.privateKeyFromEnv ? "私钥已安装 (环境变量)" : "私钥已安装";
    keyStatus.className = "status-pill ok";
  } else {
    keyStatus.textContent = "发证私钥未安装";
    keyStatus.className = "status-pill danger";
    setResult("发证私钥未安装，无法导出授权文件。", "danger");
  }
  updateSummary();
}

chooseOutput.addEventListener("click", async () => {
  const selected = await window.taijiLicenseIssuer.chooseOutput();
  if (!selected.canceled && selected.filePath) {
    outputPath.value = selected.filePath;
  }
});

resetBtn.addEventListener("click", () => {
  form.reset();
  document.getElementById("days").value = "30";
  document.getElementById("features").value = "chat,writing";
  loadStatus().catch((err) => setResult(err.message || String(err), "danger"));
});

form.addEventListener("input", updateSummary);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setResult("正在生成...", "muted");
  const response = await window.taijiLicenseIssuer.generate(readForm());
  if (!response.ok) {
    setResult(response.error || "生成失败", "danger");
    return;
  }
  setResult(
    [
      `已导出：${response.outputPath}`,
      `到期时间：${response.payload.expires_at}`,
      `签发记录：${response.recordPath}`,
      `摘要：${response.tokenHash}`,
    ].join("\n"),
    "ok",
  );
});

loadStatus().catch((err) => {
  keyStatus.textContent = "状态读取失败";
  keyStatus.className = "status-pill danger";
  setResult(err.message || String(err), "danger");
});

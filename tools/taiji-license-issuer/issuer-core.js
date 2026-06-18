"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PRODUCT = "taiji-agent";
const PRIVATE_KEY_ENV = "TAIJI_LICENSE_PRIVATE_KEY_FILE";
const DEFAULT_PRIVATE_KEY_NAME = "signing-private.pem";
const DEFAULT_PUBLIC_KEY_NAME = "signing-public.pem";
const MACHINE_BINDING_TYPE = "machine_fingerprint_v2";
const MACHINE_REQUEST_TYPE = "taiji_machine_license_request";
const MACHINE_CODE_RE = /^sha256:[0-9a-f]{64}$/;
const ACTIVATION_MODE_OFFLINE_MACHINE_FILE = "offline_machine_file";

function isoUtc(date) {
  return date.toISOString().replace(".000Z", "Z");
}

function base64Url(input) {
  return Buffer.from(input)
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function parseUtcDate(value, fallback) {
  if (!value) {
    return new Date(fallback.getTime());
  }
  let text = String(value).trim();
  if (!text) {
    return new Date(fallback.getTime());
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    text = `${text}T00:00:00Z`;
  } else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) {
    text = `${text}:00Z`;
  } else if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) {
    text = `${text}Z`;
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("起始时间格式无效");
  }
  return parsed;
}

function parseDays(value) {
  const days = Number(value);
  if (!Number.isInteger(days) || days <= 0) {
    throw new Error("有效天数必须大于 0");
  }
  return days;
}

function parseFeatures(value) {
  const features = Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean)
    : String(value || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
  if (!features.length) {
    throw new Error("功能包不能为空");
  }
  return features;
}

function resolvePrivateKeyPath(options = {}) {
  const env = options.env || process.env;
  if (env[PRIVATE_KEY_ENV] && String(env[PRIVATE_KEY_ENV]).trim()) {
    return path.resolve(String(env[PRIVATE_KEY_ENV]).trim());
  }
  return path.join(__dirname, "private", DEFAULT_PRIVATE_KEY_NAME);
}

function resolvePublicKeyPath(options = {}) {
  const privateKeyPath = options.privateKeyPath || resolvePrivateKeyPath(options);
  return path.join(path.dirname(privateKeyPath), DEFAULT_PUBLIC_KEY_NAME);
}

function defaultRecordPath() {
  return path.join(os.homedir(), "Library", "Application Support", "Taiji License Issuer", "issued_licenses.jsonl");
}

function sha256Hex(value) {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

function machineCodeShort(machineCode) {
  const text = String(machineCode || "").trim();
  if (text.startsWith("sha256:")) {
    return text.slice("sha256:".length, "sha256:".length + 12);
  }
  return text.slice(0, 12);
}

function sanitizeFilePart(value, fallback) {
  const text = String(value || "")
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "");
  return text || fallback;
}

function normalizeMachineRequest(value) {
  if (!value || typeof value !== "object") {
    throw new Error("请先导入机器码文件");
  }
  const product = String(value.product || PRODUCT).trim();
  if (product !== PRODUCT) {
    throw new Error("机器码文件产品不匹配");
  }
  const bindingType = String(value.binding_type || value.bindingType || "").trim();
  if (bindingType !== MACHINE_BINDING_TYPE) {
    throw new Error("机器码文件绑定类型无效");
  }
  const machineCode = String(value.machine_code || value.machineCode || "").trim().toLowerCase();
  if (!MACHINE_CODE_RE.test(machineCode)) {
    throw new Error("机器码文件无效");
  }
  const machineLabel = String(value.machine_label || value.machineLabel || value.terminal_note || value.hostname || "").trim();
  return {
    requestType: String(value.request_type || value.requestType || MACHINE_REQUEST_TYPE).trim(),
    product,
    bindingType,
    machineCode,
    machineCodeShort: String(value.machine_code_short || value.machineCodeShort || machineCodeShort(machineCode)).trim() || machineCodeShort(machineCode),
    machineLabel,
    hostname: String(value.hostname || "").trim(),
    generatedAt: String(value.generated_at || value.generatedAt || "").trim(),
    sourcePath: value.sourcePath || "",
  };
}

function machineRequestFromOptions(options) {
  if (options.machineRequest) {
    return normalizeMachineRequest(options.machineRequest);
  }
  if (!options.machineCode) {
    throw new Error("请先导入机器码文件");
  }
  return normalizeMachineRequest({
    product: PRODUCT,
    binding_type: options.bindingType || MACHINE_BINDING_TYPE,
    machine_code: options.machineCode,
    machine_code_short: options.machineCodeShort,
    machine_label: options.machineLabel,
  });
}

function parseMachineRequest(content, sourcePath = "") {
  let data;
  try {
    data = JSON.parse(String(content || ""));
  } catch (_) {
    throw new Error("机器码文件不是合法 JSON");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("机器码文件格式无效");
  }
  return normalizeMachineRequest({ ...data, sourcePath });
}

function readMachineRequestFile(filePath) {
  const resolved = path.resolve(String(filePath || ""));
  const content = fs.readFileSync(resolved, "utf8");
  return parseMachineRequest(content, resolved);
}

function readMachineRequestDirectory(dirPath) {
  const root = path.resolve(String(dirPath || ""));
  const entries = fs
    .readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".json"))
    .map((entry) => path.join(root, entry.name))
    .sort();
  const requests = entries.map((filePath) => readMachineRequestFile(filePath));
  if (!requests.length) {
    throw new Error("目录中没有机器码 JSON 文件");
  }
  return requests;
}

function signJwt(payload, privateKeyPem) {
  const header = { alg: "RS256", typ: "JWT" };
  const signingInput = `${base64Url(JSON.stringify(header))}.${base64Url(JSON.stringify(payload))}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(signingInput);
  signer.end();
  const signature = signer
    .sign(privateKeyPem)
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
  return `${signingInput}.${signature}`;
}

function issueLicense(options) {
  const customer = String(options.customer || "").trim();
  if (!customer) {
    throw new Error("客户名称不能为空");
  }
  const days = parseDays(options.days);
  const features = parseFeatures(options.features);
  const privateKeyPem = String(options.privateKeyPem || "").trim();
  if (!privateKeyPem) {
    throw new Error("发证私钥未安装");
  }
  const machineRequest = machineRequestFromOptions(options);

  const now = options.now ? new Date(options.now) : new Date();
  if (Number.isNaN(now.getTime())) {
    throw new Error("签发时间无效");
  }
  const nbf = parseUtcDate(options.notBefore, now);
  const exp = new Date(nbf.getTime() + days * 86400 * 1000);
  const licenseId = String(options.licenseId || "").trim() || `lic-${Math.floor(now.getTime() / 1000)}`;
  const payload = {
    license_id: licenseId,
    customer,
    product: PRODUCT,
    aud: PRODUCT,
    iat: Math.floor(now.getTime() / 1000),
    issued_at: isoUtc(now),
    nbf: Math.floor(nbf.getTime() / 1000),
    not_before: isoUtc(nbf),
    exp: Math.floor(exp.getTime() / 1000),
    expires_at: isoUtc(exp),
    features,
    activation_mode: ACTIVATION_MODE_OFFLINE_MACHINE_FILE,
    binding_type: MACHINE_BINDING_TYPE,
    machine_code: machineRequest.machineCode,
  };
  if (machineRequest.machineLabel) {
    payload.machine_label = machineRequest.machineLabel;
  }
  const maxVersion = String(options.maxVersion || "").trim();
  if (maxVersion) {
    payload.max_version = maxVersion;
  }
  const token = signJwt(payload, privateKeyPem);
  return {
    token,
    payload,
    tokenHash: `sha256:${sha256Hex(token)}`,
  };
}

function readPrivateKey(privateKeyPath) {
  try {
    return fs.readFileSync(privateKeyPath, "utf8");
  } catch (err) {
    throw new Error(`发证私钥未安装：${privateKeyPath}`);
  }
}

function writeFile0600(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(filePath, content, { encoding: "utf8", mode: 0o600 });
  try {
    fs.chmodSync(filePath, 0o600);
  } catch (_) {
    // chmod can be unavailable on some filesystems; the file contents are still written.
  }
}

function writePublicKeyFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(filePath, content, { encoding: "utf8", mode: 0o644 });
  try {
    fs.chmodSync(filePath, 0o644);
  } catch (_) {
    // Public keys are not secret; chmod is best effort for filesystem portability.
  }
}

function initializeSigningKeyPair(options = {}) {
  const privateKeyPath = path.resolve(options.privateKeyPath || resolvePrivateKeyPath());
  const publicKeyPath = path.resolve(options.publicKeyPath || resolvePublicKeyPath({ privateKeyPath }));
  if (fs.existsSync(privateKeyPath) && !options.overwrite) {
    throw new Error(`发证私钥已存在：${privateKeyPath}`);
  }
  const keys = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const privateKeyPem = keys.privateKey.export({ type: "pkcs8", format: "pem" });
  const publicKeyPem = keys.publicKey.export({ type: "spki", format: "pem" });
  writeFile0600(privateKeyPath, privateKeyPem);
  writePublicKeyFile(publicKeyPath, publicKeyPem);
  return {
    privateKeyPath,
    publicKeyPath,
    publicKeyPem,
  };
}

function appendIssueRecord(recordPath, record) {
  fs.mkdirSync(path.dirname(recordPath), { recursive: true, mode: 0o700 });
  fs.appendFileSync(recordPath, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
  try {
    fs.chmodSync(recordPath, 0o600);
  } catch (_) {
    // Keep issuing usable even if chmod is not supported.
  }
}

function recordForIssue({ result, outputPath, now, machineRequest }) {
  return {
    generated_at: isoUtc(now),
    license_id: result.payload.license_id,
    customer: result.payload.customer,
    not_before: result.payload.not_before,
    expires_at: result.payload.expires_at,
    features: result.payload.features,
    activation_mode: result.payload.activation_mode || "",
    max_version: result.payload.max_version || "",
    machine_code_short: machineRequest.machineCodeShort,
    machine_label: machineRequest.machineLabel || "",
    output_path: outputPath,
    jwt_hash: result.tokenHash,
  };
}

function issueAndWriteLicense(options) {
  const privateKeyPath = options.privateKeyPath || resolvePrivateKeyPath();
  const privateKeyPem = readPrivateKey(privateKeyPath);
  const machineRequest = machineRequestFromOptions(options);
  const result = issueLicense({ ...options, privateKeyPem, machineRequest });
  const rawOutputPath = String(options.outputPath || "").trim();
  if (!rawOutputPath) {
    throw new Error("输出路径不能为空");
  }
  const outputPath = path.resolve(rawOutputPath);
  writeFile0600(outputPath, `${result.token}\n`);

  const recordPath = options.recordPath || defaultRecordPath();
  const record = recordForIssue({
    result,
    outputPath,
    now: options.now ? new Date(options.now) : new Date(),
    machineRequest,
  });
  appendIssueRecord(recordPath, record);
  return { ...result, outputPath, recordPath, record };
}

function crc32Buffer(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let index = 0; index < 8; index += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date) {
  const year = Math.max(1980, date.getFullYear());
  const timeValue = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dateValue = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { timeValue, dateValue };
}

function createStoreZip(files, now = new Date()) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  const { timeValue, dateValue } = dosDateTime(now);
  for (const file of files) {
    const name = Buffer.from(file.name, "utf8");
    const data = Buffer.from(file.content, "utf8");
    const crc = crc32Buffer(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(timeValue, 10);
    local.writeUInt16LE(dateValue, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, name, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(timeValue, 12);
    central.writeUInt16LE(dateValue, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0o600 << 16, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, name);
    offset += local.length + name.length + data.length;
  }
  const centralOffset = offset;
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(centralOffset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, end]);
}

function issueBatchZip(options) {
  const privateKeyPath = options.privateKeyPath || resolvePrivateKeyPath();
  const privateKeyPem = readPrivateKey(privateKeyPath);
  const machineRequests = (options.machineRequests || []).map((request) => normalizeMachineRequest(request));
  if (!machineRequests.length) {
    throw new Error("请先导入机器码文件");
  }
  const rawOutputPath = String(options.outputPath || "").trim();
  if (!rawOutputPath) {
    throw new Error("输出路径不能为空");
  }
  const outputPath = path.resolve(rawOutputPath);
  const now = options.now ? new Date(options.now) : new Date();
  const recordPath = options.recordPath || defaultRecordPath();
  const files = [];
  const records = [];
  machineRequests.forEach((machineRequest, index) => {
    const shortCode = machineRequest.machineCodeShort || machineCodeShort(machineRequest.machineCode);
    const licenseIdBase = String(options.licenseId || "").trim();
    const licenseId = licenseIdBase
      ? `${licenseIdBase}-${shortCode}`
      : `lic-${Math.floor(now.getTime() / 1000)}-${shortCode}`;
    const result = issueLicense({
      ...options,
      privateKeyPem,
      machineRequest,
      licenseId,
      now,
    });
    const safePart = sanitizeFilePart(machineRequest.machineLabel, shortCode || String(index + 1));
    const fileName = `license-${safePart}.jwt`;
    files.push({ name: fileName, content: `${result.token}\n`, payload: result.payload, tokenHash: result.tokenHash });
    records.push(
      recordForIssue({
        result,
        outputPath: `${outputPath}#${fileName}`,
        now,
        machineRequest,
      }),
    );
  });
  fs.mkdirSync(path.dirname(outputPath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(outputPath, createStoreZip(files, now), { mode: 0o600 });
  try {
    fs.chmodSync(outputPath, 0o600);
  } catch (_) {
    // Keep issuing usable even if chmod is not supported.
  }
  for (const record of records) {
    appendIssueRecord(recordPath, record);
  }
  return {
    outputPath,
    recordPath,
    files: files.map((file) => ({
      name: file.name,
      license_id: file.payload.license_id,
      machine_code_short: machineCodeShort(file.payload.machine_code),
      tokenHash: file.tokenHash,
    })),
  };
}

module.exports = {
  PRODUCT,
  PRIVATE_KEY_ENV,
  defaultRecordPath,
  initializeSigningKeyPair,
  issueBatchZip,
  issueAndWriteLicense,
  issueLicense,
  normalizeMachineRequest,
  parseFeatures,
  parseMachineRequest,
  parseUtcDate,
  readMachineRequestDirectory,
  readMachineRequestFile,
  resolvePrivateKeyPath,
  resolvePublicKeyPath,
};

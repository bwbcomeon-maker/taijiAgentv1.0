"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PRODUCT = "taiji-agent";
const PRIVATE_KEY_ENV = "TAIJI_LICENSE_PRIVATE_KEY_FILE";
const DEFAULT_PRIVATE_KEY_NAME = "signing-private.pem";
const DEFAULT_PUBLIC_KEY_NAME = "signing-public.pem";
const MACHINE_BINDING_TYPE = "machine_fingerprint_v3";
const MACHINE_REQUEST_TYPE = "taiji_machine_license_request";
const MACHINE_CODE_RE = /^sha256:[0-9a-f]{64}$/;
const ACTIVATION_MODE_OFFLINE_MACHINE_FILE = "offline_machine_file";
const BLOCKING_RISK_FLAGS = new Set(["no_device_secret", "device_secret_unavailable", "no_stable_hardware"]);
const REQUIRED_FEATURES_FIELD = "required_features";
const REQUIRED_FEATURES_INTERNAL_FIELD = "requiredFeatures";

// 产品识别表：机器请求声明 -> 授权标签只允许这里的已知映射，不照抄请求标签。
// 两个产品共用 product/aud = taiji-agent 线协议，产品只能由 required_features 判定。
const PRODUCT_LICENSES = [
  {
    productId: "kongtian_agent",
    productName: "国网空天智能体",
    knownFeatures: new Set(["kongtian_agent"]),
    features: ["kongtian_agent"],
  },
  {
    productId: "taiji_agent",
    productName: "太极智能体",
    knownFeatures: new Set(["chat", "writing"]),
    features: ["chat", "writing"],
  },
];
const LEGACY_TAIJI_LICENSE = {
  productId: "taiji_agent",
  productName: "太极智能体",
  features: ["chat", "writing"],
};

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

// 读取机器请求中的产品声明。字段缺失返回 undefined（旧太极格式，允许兼容回退）；
// 字段存在但结构无效（null、非数组、空数组、非字符串或纯空白成员）直接拒绝。
function extractRequiredFeatures(value) {
  const hasExternal = value[REQUIRED_FEATURES_FIELD] !== undefined;
  const hasInternal = value[REQUIRED_FEATURES_INTERNAL_FIELD] !== undefined;
  if (!hasExternal && !hasInternal) {
    return undefined;
  }
  const declarations = [];
  if (hasExternal) {
    declarations.push(normalizeFeatureDeclaration(value[REQUIRED_FEATURES_FIELD]));
  }
  if (hasInternal) {
    declarations.push(normalizeFeatureDeclaration(value[REQUIRED_FEATURES_INTERNAL_FIELD]));
  }
  if (declarations.length === 2 && declarations[0].join(",") !== declarations[1].join(",")) {
    throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 与 ${REQUIRED_FEATURES_INTERNAL_FIELD} 声明不一致`);
  }
  return declarations[0];
}

function normalizeFeatureDeclaration(declaration) {
  if (declaration === null || typeof declaration !== "object" || !Array.isArray(declaration)) {
    throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 声明无效：需为字符串数组`);
  }
  const labels = [];
  for (const item of declaration) {
    if (typeof item !== "string" || !item.trim()) {
      throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 声明无效：需为字符串数组`);
    }
    labels.push(item.trim());
  }
  const unique = [...new Set(labels)];
  if (!unique.length) {
    throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 声明无效：不能为空数组`);
  }
  return unique;
}

// 统一产品识别：输入原始或已规范化的机器请求均可，结果只来自已知映射。
function resolveProductLicense(machineRequest) {
  const normalized = normalizeMachineRequest(machineRequest);
  const declared = normalized.requiredFeatures;
  if (declared === undefined) {
    return {
      ...LEGACY_TAIJI_LICENSE,
      basis: "缺少 required_features（旧版机器码，按太极兼容处理）",
    };
  }
  const matches = PRODUCT_LICENSES.filter(
    (license) => declared.every((label) => license.knownFeatures.has(label)),
  );
  if (matches.length === 1) {
    const license = matches[0];
    return {
      productId: license.productId,
      productName: license.productName,
      features: [...license.features],
      basis: `required_features=[${declared.join(",")}]`,
    };
  }
  const knownLabels = new Set(PRODUCT_LICENSES.flatMap((license) => [...license.knownFeatures]));
  const unknown = declared.filter((label) => !knownLabels.has(label));
  if (unknown.length) {
    throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 含不支持的产品标签：${unknown.join(",")}`);
  }
  throw new Error(`机器码文件 ${REQUIRED_FEATURES_FIELD} 声明冲突：混合了多个产品的标签`);
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
  return (text || fallback).slice(0, 72);
}

function compactTimestamp(value, fallback = new Date()) {
  const date = value instanceof Date ? value : parseUtcDate(value, fallback);
  return isoUtc(date).replace(/[-:]/g, "").replace("T", "-");
}

function licenseFileName({ customer, machineRequest, notBefore, days, now }) {
  const nbf = parseUtcDate(notBefore, now || new Date());
  const exp = new Date(nbf.getTime() + parseDays(days) * 86400 * 1000);
  const customerPart = sanitizeFilePart(customer, "customer");
  const machinePart = sanitizeFilePart(machineRequest.machineLabel || machineRequest.hostname, "terminal");
  const shortCode = sanitizeFilePart(machineRequest.machineCodeShort || machineCodeShort(machineRequest.machineCode), "machine");
  return [
    "taiji-license",
    customerPart,
    machinePart,
    shortCode,
    compactTimestamp(nbf),
    compactTimestamp(exp),
  ].join("-") + ".jwt";
}

function batchZipFileName({ customer, count, now }) {
  const customerPart = sanitizeFilePart(customer, "customer");
  return `taiji-licenses-${customerPart}-${count}台-${compactTimestamp(now || new Date())}.zip`;
}

function isGenericOutputName(filePath, batch) {
  const base = path.basename(String(filePath || "")).toLowerCase();
  const generic = batch
    ? new Set(["taiji-licenses.zip", "licenses.zip"])
    : new Set(["license.jwt", "taiji-license.jwt"]);
  return generic.has(base);
}

function resolveDescriptiveOutputPath(rawOutputPath, suggestedName, batch) {
  const raw = String(rawOutputPath || "").trim();
  if (!raw) {
    throw new Error("输出路径不能为空");
  }
  const resolved = path.resolve(raw);
  if (isGenericOutputName(resolved, batch)) {
    return path.join(path.dirname(resolved), suggestedName);
  }
  return resolved;
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
    throw new Error("机器码文件不是新版机器码，请在客户机重新导出机器码文件");
  }
  const machineCode = String(value.machine_code || value.machineCode || "").trim().toLowerCase();
  if (!MACHINE_CODE_RE.test(machineCode)) {
    throw new Error("机器码文件无效");
  }
  const deviceId = String(value.device_id || value.deviceId || "").trim().toLowerCase();
  if (!MACHINE_CODE_RE.test(deviceId)) {
    throw new Error("新版机器码缺少设备身份，请在客户机重新导出机器码文件");
  }
  const riskFlags = Array.isArray(value.risk_flags || value.riskFlags)
    ? (value.risk_flags || value.riskFlags).map((item) => String(item).trim()).filter(Boolean)
    : [];
  const blockingRisk = riskFlags.find((item) => BLOCKING_RISK_FLAGS.has(item));
  if (blockingRisk) {
    throw new Error(`机器码质量不足，不能签发离线授权：${blockingRisk}`);
  }
  const machineLabel = String(value.machine_label || value.machineLabel || value.terminal_note || value.hostname || "").trim();
  const requiredFeatures = extractRequiredFeatures(value);
  const normalized = {
    schemaVersion: Number(value.schema_version || value.schemaVersion || 0),
    requestId: String(value.request_id || value.requestId || "").trim(),
    requestType: String(value.request_type || value.requestType || MACHINE_REQUEST_TYPE).trim(),
    product,
    bindingType,
    machineCode,
    machineCodeShort: String(value.machine_code_short || value.machineCodeShort || machineCodeShort(machineCode)).trim() || machineCodeShort(machineCode),
    deviceId,
    deviceIdShort: String(value.device_id_short || value.deviceIdShort || machineCodeShort(deviceId)).trim() || machineCodeShort(deviceId),
    hardwareCodeShort: String(value.hardware_code_short || value.hardwareCodeShort || "").trim(),
    fingerprintQuality: String(value.fingerprint_quality || value.fingerprintQuality || "unknown").trim(),
    riskFlags,
    machineLabel,
    hostname: String(value.hostname || "").trim(),
    generatedAt: String(value.generated_at || value.generatedAt || "").trim(),
    sourcePath: value.sourcePath || "",
  };
  // 旧格式没有产品声明时保持字段缺失，识别层才能区分“缺失”与“无效”。
  if (requiredFeatures !== undefined) {
    normalized.requiredFeatures = requiredFeatures;
  }
  return normalized;
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

function stripUtf8Bom(content) {
  return content.charCodeAt(0) === 0xfeff ? content.slice(1) : content;
}

function parseMachineRequest(content, sourcePath = "") {
  let data;
  try {
    data = JSON.parse(stripUtf8Bom(String(content || "")));
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
  const privateKeyPem = String(options.privateKeyPem || "").trim();
  if (!privateKeyPem) {
    throw new Error("发证私钥未安装");
  }
  const machineRequest = machineRequestFromOptions(options);
  // 授权标签只来自机器请求的产品声明；options.features 不再参与本链路，
  // 即使旧调用方仍传入默认值也不能覆盖识别结果。
  const productLicense = resolveProductLicense(machineRequest);
  const features = productLicense.features;

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
    machine_code_short: machineRequest.machineCodeShort,
    device_id: machineRequest.deviceId,
    device_id_short: machineRequest.deviceIdShort,
    machine_request_id: machineRequest.requestId || "",
    machine_request_generated_at: machineRequest.generatedAt || "",
    fingerprint_quality: machineRequest.fingerprintQuality || "unknown",
    risk_flags: machineRequest.riskFlags || [],
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
    productLicense,
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
  const productLicense = result.productLicense;
  return {
    generated_at: isoUtc(now),
    license_id: result.payload.license_id,
    customer: result.payload.customer,
    product_id: productLicense ? productLicense.productId : "",
    product_name: productLicense ? productLicense.productName : "",
    recognition_basis: productLicense ? productLicense.basis : "",
    not_before: result.payload.not_before,
    expires_at: result.payload.expires_at,
    features: result.payload.features,
    activation_mode: result.payload.activation_mode || "",
    max_version: result.payload.max_version || "",
    machine_code_short: machineRequest.machineCodeShort,
    device_id_short: machineRequest.deviceIdShort,
    hardware_code_short: machineRequest.hardwareCodeShort || "",
    fingerprint_quality: machineRequest.fingerprintQuality || "unknown",
    risk_flags: machineRequest.riskFlags || [],
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
  const outputPath = resolveDescriptiveOutputPath(
    options.outputPath,
    licenseFileName({
      customer: options.customer,
      machineRequest,
      notBefore: options.notBefore,
      days: options.days,
      now: options.now ? new Date(options.now) : new Date(),
    }),
    false,
  );
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
    local.writeUInt16LE(0x0800, 6);
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
    central.writeUInt16LE(0x0800, 8);
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
  const machineRequests = (options.machineRequests || []).map((request) => normalizeMachineRequest(request));
  if (!machineRequests.length) {
    throw new Error("请先导入机器码文件");
  }
  const seenCodes = new Map();
  for (const request of machineRequests) {
    if (seenCodes.has(request.machineCode)) {
      throw new Error(`检测到重复机器码：${request.machineCodeShort || machineCodeShort(request.machineCode)}`);
    }
    seenCodes.set(request.machineCode, request);
  }
  // 签发前对整批逐份完成产品识别预检查：任何一份异常都整批拒绝并指出出错文件，
  // 不写出 ZIP、不追加任何成功记录。
  machineRequests.forEach((request) => {
    try {
      resolveProductLicense(request);
    } catch (err) {
      const filePart = request.sourcePath ? path.basename(request.sourcePath) : request.machineCodeShort;
      throw new Error(`${filePart}：${err.message}`);
    }
  });
  const privateKeyPath = options.privateKeyPath || resolvePrivateKeyPath();
  const privateKeyPem = readPrivateKey(privateKeyPath);
  const now = options.now ? new Date(options.now) : new Date();
  const outputPath = resolveDescriptiveOutputPath(
    options.outputPath,
    batchZipFileName({ customer: options.customer, count: machineRequests.length, now }),
    true,
  );
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
    const fileName = licenseFileName({
      customer: options.customer,
      machineRequest,
      notBefore: options.notBefore,
      days: options.days,
      now,
    });
    files.push({ name: fileName, content: `${result.token}\n`, payload: result.payload, productLicense: result.productLicense, tokenHash: result.tokenHash });
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
      product_id: file.productLicense.productId,
      product_name: file.productLicense.productName,
      features: file.payload.features,
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
  licenseFileName,
  normalizeMachineRequest,
  parseMachineRequest,
  parseUtcDate,
  readMachineRequestDirectory,
  readMachineRequestFile,
  resolvePrivateKeyPath,
  resolveProductLicense,
  resolvePublicKeyPath,
};

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PRODUCT = "taiji-agent";
const PRIVATE_KEY_ENV = "TAIJI_LICENSE_PRIVATE_KEY_FILE";
const DEFAULT_PRIVATE_KEY_NAME = "signing-private.pem";

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

function defaultRecordPath() {
  return path.join(os.homedir(), "Library", "Application Support", "Taiji License Issuer", "issued_licenses.jsonl");
}

function sha256Hex(value) {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
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
  };
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

function appendIssueRecord(recordPath, record) {
  fs.mkdirSync(path.dirname(recordPath), { recursive: true, mode: 0o700 });
  fs.appendFileSync(recordPath, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
  try {
    fs.chmodSync(recordPath, 0o600);
  } catch (_) {
    // Keep issuing usable even if chmod is not supported.
  }
}

function issueAndWriteLicense(options) {
  const privateKeyPath = options.privateKeyPath || resolvePrivateKeyPath();
  const privateKeyPem = readPrivateKey(privateKeyPath);
  const result = issueLicense({ ...options, privateKeyPem });
  const rawOutputPath = String(options.outputPath || "").trim();
  if (!rawOutputPath) {
    throw new Error("输出路径不能为空");
  }
  const outputPath = path.resolve(rawOutputPath);
  writeFile0600(outputPath, `${result.token}\n`);

  const recordPath = options.recordPath || defaultRecordPath();
  const record = {
    generated_at: isoUtc(options.now ? new Date(options.now) : new Date()),
    license_id: result.payload.license_id,
    customer: result.payload.customer,
    not_before: result.payload.not_before,
    expires_at: result.payload.expires_at,
    features: result.payload.features,
    max_version: result.payload.max_version || "",
    output_path: outputPath,
    jwt_hash: result.tokenHash,
  };
  appendIssueRecord(recordPath, record);
  return { ...result, outputPath, recordPath, record };
}

module.exports = {
  PRODUCT,
  PRIVATE_KEY_ENV,
  defaultRecordPath,
  issueAndWriteLicense,
  issueLicense,
  parseFeatures,
  parseUtcDate,
  resolvePrivateKeyPath,
};

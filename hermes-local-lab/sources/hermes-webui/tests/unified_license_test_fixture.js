"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");


const PRIVATE_KEY_ENV = "TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE";
const ACCOUNT_HOME_HOOK_ENV = "TAIJI_LICENSE_TEST_ACCOUNT_HOME";
const CHAINED_SITE_HOOK_ENV = "TAIJI_LICENSE_TEST_CHAINED_SITE_DIRS";
const SIGNER_FILE_ERROR = "The unified license test signer is unavailable or unsafe";
const FIXTURE_CLEANUP_ERROR = "The unified license fixture profile could not be removed";
const SECURITY_OVERRIDE_ENV_NAMES = Object.freeze([
  "TAIJI_LICENSE_REQUIRED",
  "TAIJI_LICENSE_FILE",
  "TAIJI_LICENSE_STATE_FILE",
  "TAIJI_LICENSE_PUBLIC_KEY",
  "TAIJI_LICENSE_PUBLIC_KEY_FILE",
  "TAIJI_LICENSE_MACHINE_BINDING_REQUIRED",
  "TAIJI_LICENSE_DEVICE_FILE",
  "TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING",
  "TAIJI_AGENT_VERSION",
  "TAIJI_LICENSE_PRIVATE_KEY_FILE",
  PRIVATE_KEY_ENV,
  ACCOUNT_HOME_HOOK_ENV,
  CHAINED_SITE_HOOK_ENV,
]);
const SANITIZED_ENV_NAMES = new Set([
  ...SECURITY_OVERRIDE_ENV_NAMES,
  "PYTHONHOME",
  "PYTHONPATH",
].map((name) => name.toUpperCase()));
const signerSecrets = new WeakMap();
const issuedSecrets = new WeakMap();
const fixtureRecords = new WeakMap();
const activeFixtureRecords = new Set();
let exitCleanupRegistered = false;


function sanitizeUnifiedLicenseRuntimeEnv(baseEnv = {}) {
  const runtimeEnv = {};
  for (const [name, value] of Object.entries(baseEnv)) {
    if (!SANITIZED_ENV_NAMES.has(name.toUpperCase())) runtimeEnv[name] = value;
  }
  return runtimeEnv;
}


function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}


function runPython({ pythonBin, agentDir, environ, args }) {
  const result = spawnSync(pythonBin, args, {
    cwd: agentDir,
    env: environ,
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
  });
  if (result.status !== 0) {
    throw new Error("The selected Agent Python could not validate the unified license fixture");
  }
  return String(result.stdout || "").trim();
}


function loadUnifiedLicenseTestSigner({ repoRoot, agentDir, pythonBin, environ = process.env }) {
  const rawKeyPath = String(environ[PRIVATE_KEY_ENV] || "").trim();
  if (!rawKeyPath) throw new Error(`${PRIVATE_KEY_ENV} is required by the test harness`);

  const keyPath = path.resolve(repoRoot, rawKeyPath);
  let privateKeyPem;
  try {
    const keyRealpath = fs.realpathSync(keyPath);
    const metadata = fs.lstatSync(keyPath);
    if (
      keyRealpath !== keyPath
      || metadata.isSymbolicLink()
      || !metadata.isFile()
      || (metadata.mode & 0o777) !== 0o600
    ) {
      throw new Error(SIGNER_FILE_ERROR);
    }
    privateKeyPem = fs.readFileSync(keyPath, "utf8");
  } catch (_) {
    throw new Error(SIGNER_FILE_ERROR);
  }
  let publicKey;
  try {
    publicKey = crypto.createPublicKey(privateKeyPem);
  } catch (_) {
    throw new Error(SIGNER_FILE_ERROR);
  }
  const spki = publicKey.export({ type: "spki", format: "der" });
  const actualFingerprint = crypto.createHash("sha256").update(spki).digest();

  const queryEnv = sanitizeUnifiedLicenseRuntimeEnv(environ);
  queryEnv.PYTHONPATH = agentDir;
  const expectedText = runPython({
    pythonBin,
    agentDir,
    environ: queryEnv,
    args: [
      "-c",
      "import taiji_license; print(taiji_license.PRODUCTION_PUBLIC_KEY_FINGERPRINT)",
    ],
  });
  if (!/^[0-9a-f]{64}$/.test(expectedText)) {
    throw new Error("The selected Agent returned an invalid production public-key fingerprint");
  }
  const expectedFingerprint = Buffer.from(expectedText, "hex");
  if (
    actualFingerprint.length !== expectedFingerprint.length
    || !crypto.timingSafeEqual(actualFingerprint, expectedFingerprint)
  ) {
    throw new Error("The unified license test signer does not match the Agent production key");
  }

  const signer = Object.freeze({ publicKeyFingerprint: expectedText });
  signerSecrets.set(signer, { privateKeyPem });
  return signer;
}


function issueUnifiedLicenseForMachineRequest({ repoRoot, machineRequest, signer }) {
  const secret = signerSecrets.get(signer);
  if (!secret) throw new Error("The unified license signer was not loaded by this harness");
  const issuer = require(path.join(repoRoot, "tools", "taiji-license-issuer", "issuer-core.js"));
  const normalized = issuer.normalizeMachineRequest(machineRequest);
  if (
    normalized.schemaVersion !== 3
    || normalized.bindingType !== "machine_fingerprint_v3"
    || normalized.fingerprintQuality !== "strong"
    || normalized.riskFlags.length !== 0
  ) {
    throw new Error("The unified license fixture requires a strong, risk-free V3 machine request");
  }

  const version = fs.readFileSync(path.join(repoRoot, "VERSION"), "utf8").trim();
  if (!/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/.test(version)) {
    throw new Error("The repository VERSION is not a stable semantic version");
  }
  const now = new Date();
  const issued = issuer.issueLicense({
    customer: "Taiji unified runtime test",
    days: 1,
    features: ["chat"],
    privateKeyPem: secret.privateKeyPem,
    machineRequest,
    maxVersion: version,
    now,
    notBefore: now.toISOString(),
  });
  const result = Object.freeze({
    policyFixture: true,
    publicKeyFingerprintShort: signer.publicKeyFingerprint.slice(0, 12),
    expiresAt: issued.payload.expires_at,
    bindingType: normalized.bindingType,
    fingerprintQuality: normalized.fingerprintQuality,
    riskFlags: Object.freeze([...normalized.riskFlags]),
  });
  issuedSecrets.set(result, { token: issued.token });
  return result;
}


function writeAccountHomeHook({ hookDir }) {
  const source = String.raw`
import os
import runpy
from pathlib import Path

_PROFILE_ENV = "TAIJI_LICENSE_TEST_ACCOUNT_HOME"
_CHAIN_ENV = "TAIJI_LICENSE_TEST_CHAINED_SITE_DIRS"
_raw_profile = os.environ.pop(_PROFILE_ENV, "")
_chain = os.environ.pop(_CHAIN_ENV, "")

if _raw_profile:
    _profile = Path(_raw_profile).resolve(strict=True)
    if not _profile.is_dir():
        raise RuntimeError("temporary account profile is not a directory")
    if os.name == "posix":
        import pwd

        _original_getpwuid = pwd.getpwuid

        def _temporary_account_getpwuid(uid):
            entry = _original_getpwuid(uid)
            if int(uid) != os.getuid():
                return entry
            values = list(entry)
            values[5] = str(_profile)
            return pwd.struct_passwd(values)

        pwd.getpwuid = _temporary_account_getpwuid
    elif os.name == "nt":
        import win32profile

        win32profile.GetUserProfileDirectory = lambda _token: str(_profile)
    else:
        raise RuntimeError("unsupported temporary account profile platform")

for _candidate in _chain.split(os.pathsep):
    if not _candidate:
        continue
    _sitecustomize = Path(_candidate) / "sitecustomize.py"
    if _sitecustomize.is_file():
        runpy.run_path(str(_sitecustomize), run_name="_taiji_chained_sitecustomize")
`;
  const hookPath = path.join(hookDir, "sitecustomize.py");
  fs.writeFileSync(hookPath, source.trimStart(), { encoding: "utf8", mode: 0o600, flag: "wx" });
  fs.chmodSync(hookPath, 0o600);
}


function writePythonWrapper({ profileDir, hookDir, pythonBin }) {
  const wrapperPath = path.join(profileDir, "python-with-unified-license-fixture");
  const source = [
    "#!/bin/sh",
    "set -eu",
    `export ${CHAINED_SITE_HOOK_ENV}="\${PYTHONPATH:-}"`,
    `export ${ACCOUNT_HOME_HOOK_ENV}=${shellQuote(profileDir)}`,
    `export PYTHONPATH=${shellQuote(hookDir)}\${PYTHONPATH:+:\$PYTHONPATH}`,
    `exec ${shellQuote(pythonBin)} "$@"`,
    "",
  ].join("\n");
  fs.writeFileSync(wrapperPath, source, { encoding: "utf8", mode: 0o700, flag: "wx" });
  fs.chmodSync(wrapperPath, 0o700);
  return wrapperPath;
}


function replaceEnvironment(target, sanitized) {
  for (const name of Object.keys(target)) delete target[name];
  Object.assign(target, sanitized);
}


function removeFixtureRecord(record, { suppressErrors = false } = {}) {
  if (!record || record.cleaned) return;
  for (const name of [
    "TAIJI_AGENT_PYTHON",
    "TAIJI_WEBUI_PYTHON",
    "HERMES_WEBUI_PYTHON",
  ]) {
    delete record.runtimeEnv[name];
  }
  try {
    fs.rmSync(record.profileDir, { recursive: true, force: true });
    if (fs.existsSync(record.profileDir)) {
      throw new Error(FIXTURE_CLEANUP_ERROR);
    }
  } catch (_) {
    if (!suppressErrors) throw new Error(FIXTURE_CLEANUP_ERROR);
    return;
  }
  record.cleaned = true;
  fixtureRecords.delete(record.runtimeEnv);
  activeFixtureRecords.delete(record);
}


function registerFixtureRecord(runtimeEnv, profileDir) {
  const record = { runtimeEnv, profileDir, cleaned: false };
  fixtureRecords.set(runtimeEnv, record);
  activeFixtureRecords.add(record);
  if (!exitCleanupRegistered) {
    process.once("exit", () => {
      for (const activeRecord of [...activeFixtureRecords]) {
        removeFixtureRecord(activeRecord, { suppressErrors: true });
      }
    });
    exitCleanupRegistered = true;
  }
  return record;
}


function cleanupUnifiedLicenseFixture({ runtimeEnv } = {}) {
  if (!runtimeEnv || typeof runtimeEnv !== "object") return;
  removeFixtureRecord(fixtureRecords.get(runtimeEnv));
}


function prepareUnifiedLicenseFixture({
  repoRoot,
  agentDir,
  pythonBin,
  runtimeEnv,
}) {
  if (process.platform === "win32") {
    throw new Error(
      "Windows Electron license isolation requires a dedicated temporary OS account",
    );
  }
  if (!runtimeEnv || typeof runtimeEnv !== "object") {
    throw new Error("runtimeEnv is required for the unified license fixture");
  }
  cleanupUnifiedLicenseFixture({ runtimeEnv });
  const sanitized = sanitizeUnifiedLicenseRuntimeEnv(runtimeEnv);
  replaceEnvironment(runtimeEnv, sanitized);

  const runtimeHome = path.resolve(String(runtimeEnv.TAIJI_RUNTIME_HOME || os.tmpdir()));
  fs.mkdirSync(runtimeHome, { recursive: true, mode: 0o700 });
  const profileDir = fs.mkdtempSync(path.join(runtimeHome, "license-account-"));
  fs.chmodSync(profileDir, 0o700);
  registerFixtureRecord(runtimeEnv, profileDir);
  try {
    const hookDir = path.join(profileDir, ".python-account-hook");
    fs.mkdirSync(hookDir, { mode: 0o700 });
    fs.chmodSync(hookDir, 0o700);
    writeAccountHomeHook({ hookDir });
    const wrapperPath = writePythonWrapper({ profileDir, hookDir, pythonBin });

    runtimeEnv.TAIJI_AGENT_PYTHON = wrapperPath;
    runtimeEnv.TAIJI_WEBUI_PYTHON = wrapperPath;
    runtimeEnv.HERMES_WEBUI_PYTHON = wrapperPath;

    const signer = loadUnifiedLicenseTestSigner({
      repoRoot,
      agentDir,
      pythonBin: wrapperPath,
      environ: process.env,
    });
    const machineRequestText = runPython({
      pythonBin: wrapperPath,
      agentDir,
      environ: runtimeEnv,
      args: [
        "-c",
        "import json, taiji_license; print(json.dumps(taiji_license.build_machine_request()))",
      ],
    });
    let machineRequest;
    try {
      machineRequest = JSON.parse(machineRequestText);
    } catch (_) {
      throw new Error("The selected Agent returned an invalid V3 machine request");
    }
    const issued = issueUnifiedLicenseForMachineRequest({ repoRoot, machineRequest, signer });
    const issuedSecret = issuedSecrets.get(issued);
    if (!issuedSecret) throw new Error("The unified license token was not retained by the harness");

    const licenseDir = path.join(profileDir, ".config", "taiji-agent", "licenses");
    fs.mkdirSync(licenseDir, { recursive: true, mode: 0o700 });
    for (const directory of [
      path.join(profileDir, ".config"),
      path.join(profileDir, ".config", "taiji-agent"),
      licenseDir,
    ]) {
      fs.chmodSync(directory, 0o700);
    }
    const licensePath = path.join(licenseDir, "active-license.jwt");
    fs.writeFileSync(licensePath, `${issuedSecret.token}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    fs.chmodSync(licensePath, 0o600);

    return Object.freeze({
      policy_fixture: issued.policyFixture,
      public_key_fingerprint_short: issued.publicKeyFingerprintShort,
      expires_at: issued.expiresAt,
      binding_type: issued.bindingType,
      fingerprint_quality: issued.fingerprintQuality,
      risk_flags: issued.riskFlags,
    });
  } catch (error) {
    cleanupUnifiedLicenseFixture({ runtimeEnv });
    throw error;
  }
}


module.exports = {
  cleanupUnifiedLicenseFixture,
  issueUnifiedLicenseForMachineRequest,
  loadUnifiedLicenseTestSigner,
  prepareUnifiedLicenseFixture,
  sanitizeUnifiedLicenseRuntimeEnv,
};

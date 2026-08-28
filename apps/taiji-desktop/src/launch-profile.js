const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_INSTALL_ROOT = "/opt/taiji-agent";
const RELEASE_MANIFEST_SCHEMA = "taiji-release-manifest/v1";
const INSTALLED_PROFILE = "installed-production";
const MAX_RELEASE_MANIFEST_BYTES = 16 * 1024;
const RELEASE_VERSION_PATTERN = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const RELEASE_COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const INSTALLED_RUNTIME_SELECTORS = [
  "TAIJI_AGENT_ROOT",
  "TAIJI_AGENT_AGENT_DIR",
  "TAIJI_AGENT_WEBUI_DIR",
  "TAIJI_AGENT_PYTHON",
  "TAIJI_WEBUI_PYTHON",
  "TAIJI_WEBUI_AGENT_DIR",
];
const CHILD_CODE_ENV_NAMES = new Set([
  "BASH_ENV",
  "ELECTRON_RUN_AS_NODE",
  "ENV",
  "NODE_OPTIONS",
  "RUBYOPT",
  "RUBYLIB",
  "PERL5OPT",
  "PERL5LIB",
  "JAVA_TOOL_OPTIONS",
  "CLASSPATH",
  "CDPATH",
  "GLOBIGNORE",
  "IFS",
  "LUA_PATH",
  "LUA_CPATH",
  "PROMPT_COMMAND",
  "SHELLOPTS",
  "BASHOPTS",
]);
const SECURITY_ALLOW_FLAGS = [
  "TAIJI_ALLOW_TERMINAL",
  "TAIJI_ALLOW_EXECUTE_CODE",
  "TAIJI_ALLOW_DELEGATE_TASK",
  "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
];

function realpath(fsModule, value) {
  const resolver = fsModule.realpathSync.native || fsModule.realpathSync;
  return path.normalize(resolver.call(fsModule.realpathSync, value));
}

function manifestMetadataMatches(left, right) {
  return ["dev", "ino", "nlink", "uid", "mode", "size", "mtimeMs", "ctimeMs"]
    .every((name) => left[name] === right[name]);
}

function assertTrustedManifestMetadata(metadata, expectedManifestUid) {
  if (!metadata.isFile() || metadata.nlink !== 1) {
    throw new Error("Release manifest must be a single-link regular file");
  }
  if (metadata.uid !== expectedManifestUid) {
    throw new Error(`Release manifest owner uid must be ${expectedManifestUid}, got: ${metadata.uid}`);
  }
  if ((metadata.mode & 0o022) !== 0) {
    throw new Error("Release manifest must not be writable by group or other users");
  }
  if (metadata.size < 1 || metadata.size > MAX_RELEASE_MANIFEST_BYTES) {
    throw new Error(`Release manifest size is outside the trusted limit: ${metadata.size}`);
  }
}

function readReleaseManifest(fsModule, manifestPath, expectedManifestUid) {
  let metadata;
  try {
    metadata = fsModule.lstatSync(manifestPath);
  } catch (error) {
    throw new Error(`Release manifest is missing: ${manifestPath}`, { cause: error });
  }
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    throw new Error(`Release manifest must be a regular file, not a symlink: ${manifestPath}`);
  }
  if (metadata.nlink !== 1) {
    throw new Error(`Release manifest has an invalid hardlink count: ${metadata.nlink}`);
  }

  const constants = fsModule.constants || fs.constants;
  if (!Number.isInteger(constants.O_NOFOLLOW)) {
    throw new Error("Release manifest cannot be opened safely because O_NOFOLLOW is unavailable");
  }
  const flags = constants.O_RDONLY | constants.O_NOFOLLOW;
  let descriptor;
  try {
    descriptor = fsModule.openSync(manifestPath, flags);
    const opened = fsModule.fstatSync(descriptor);
    assertTrustedManifestMetadata(opened, expectedManifestUid);
    if (!manifestMetadataMatches(metadata, opened)) {
      throw new Error("Release manifest changed or is not a single-link regular file");
    }
    const contents = fsModule.readFileSync(descriptor, "utf8");
    const after = fsModule.fstatSync(descriptor);
    assertTrustedManifestMetadata(after, expectedManifestUid);
    if (!manifestMetadataMatches(opened, after)) {
      throw new Error("Release manifest changed while being read");
    }
    try {
      return JSON.parse(contents);
    } catch (error) {
      throw new Error("Release manifest is not valid JSON", { cause: error });
    }
  } finally {
    if (descriptor !== undefined) fsModule.closeSync(descriptor);
  }
}

function normalizeArch(value) {
  if (value === "x64" || value === "amd64") return "amd64";
  return "";
}

function validateInstalledProfile({
  appPath,
  platform,
  arch,
  installRoot,
  fsModule,
  expectedManifestUid,
}) {
  if (platform !== "linux") {
    throw new Error(`Installed production platform must be linux, got: ${platform}`);
  }
  const runtimeArch = normalizeArch(arch);
  if (!runtimeArch) {
    throw new Error(`Installed production arch must be x64/amd64, got: ${arch}`);
  }

  let physicalRoot;
  let physicalAppPath;
  try {
    physicalRoot = realpath(fsModule, installRoot);
    physicalAppPath = realpath(fsModule, appPath);
  } catch (error) {
    throw new Error("Installed production app path or install root does not exist", { cause: error });
  }
  const expectedAppPath = path.join(physicalRoot, "apps", "taiji-desktop");
  if (physicalAppPath !== expectedAppPath) {
    throw new Error(`Installed production app path is outside the fixed install root: ${physicalAppPath}`);
  }

  const manifestPath = path.join(physicalRoot, "resources", "taiji-release-manifest.json");
  const manifest = readReleaseManifest(fsModule, manifestPath, expectedManifestUid);
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("Release manifest must be a JSON object");
  }
  if (manifest.schema !== RELEASE_MANIFEST_SCHEMA) {
    throw new Error(`Release manifest schema mismatch: ${String(manifest.schema || "")}`);
  }
  if (manifest.platform !== "linux") {
    throw new Error(`Release manifest platform mismatch: ${String(manifest.platform || "")}`);
  }
  if (normalizeArch(manifest.arch) !== runtimeArch) {
    throw new Error(`Release manifest arch mismatch: ${String(manifest.arch || "")}`);
  }
  if (typeof manifest.version !== "string" || !RELEASE_VERSION_PATTERN.test(manifest.version)) {
    throw new Error("Release manifest version must be a strict three-part SemVer string");
  }
  if (typeof manifest.commit !== "string" || !RELEASE_COMMIT_PATTERN.test(manifest.commit)) {
    throw new Error("Release manifest commit must be a full 40-character lowercase Git SHA");
  }
  const version = manifest.version;
  const commit = manifest.commit;
  if (typeof manifest.installRoot !== "string" || manifest.installRoot !== physicalRoot) {
    throw new Error(`Release manifest install root mismatch: ${String(manifest.installRoot || "")}`);
  }

  return Object.freeze({
    kind: INSTALLED_PROFILE,
    mode: INSTALLED_PROFILE,
    appPath: physicalAppPath,
    installRoot: physicalRoot,
    release: Object.freeze({ version, commit }),
  });
}

function resolveLaunchProfile({
  env = process.env,
  appPath,
  platform = process.platform,
  arch = process.arch,
  installRoot = DEFAULT_INSTALL_ROOT,
  fsModule = fs,
  expectedManifestUid = 0,
} = {}) {
  if (!appPath) throw new Error("Electron app path is required to resolve the launch profile");
  let installedByPath = false;
  try {
    const physicalRoot = realpath(fsModule, installRoot);
    const physicalAppPath = realpath(fsModule, appPath);
    installedByPath = physicalAppPath === path.join(physicalRoot, "apps", "taiji-desktop");
  } catch (_) {
    installedByPath = false;
  }
  if (installedByPath) {
    return validateInstalledProfile({
      appPath,
      platform,
      arch,
      installRoot,
      fsModule,
      expectedManifestUid,
    });
  }

  const requested = String(env.TAIJI_LAUNCH_PROFILE || "").trim();
  if (requested && requested !== "source" && requested !== INSTALLED_PROFILE) {
    throw new Error(`Unsupported launch profile: ${requested}`);
  }
  if (requested === INSTALLED_PROFILE) {
    return validateInstalledProfile({
      appPath,
      platform,
      arch,
      installRoot,
      fsModule,
      expectedManifestUid,
    });
  }

  const mode = String(env.TAIJI_SOURCE_MODE || "formal").trim();
  if (mode !== "formal" && mode !== "development") {
    throw new Error(`Unsupported source mode: ${mode}`);
  }
  return Object.freeze({ kind: "source", mode });
}

function clearInstalledCodeSelectors(runtimeEnv) {
  for (const name of Object.keys(runtimeEnv)) {
    if (
      INSTALLED_RUNTIME_SELECTORS.includes(name)
      || CHILD_CODE_ENV_NAMES.has(name)
      || name.startsWith("PYTHON")
      || name.startsWith("NODE_")
      || name.startsWith("LD_")
      || name.startsWith("DYLD_")
      || name.startsWith("BASH_FUNC_")
    ) {
      delete runtimeEnv[name];
    }
  }
}

function applyInstalledRuntimePaths({ launchProfile, runtimeEnv, fsModule = fs }) {
  if (!launchProfile || launchProfile.kind !== INSTALLED_PROFILE) return;
  const physicalRoot = realpath(fsModule, launchProfile.installRoot);
  if (physicalRoot !== launchProfile.installRoot) {
    throw new Error(`Installed runtime root is not the fixed physical install root: ${physicalRoot}`);
  }

  const expectedAgentDir = path.join(physicalRoot, "runtime", "agent");
  const expectedWebuiDir = path.join(physicalRoot, "runtime", "web");
  const expectedPython = path.join(expectedAgentDir, "venv", "bin", "python");
  const physicalAgentDir = realpath(fsModule, expectedAgentDir);
  const physicalWebuiDir = realpath(fsModule, expectedWebuiDir);
  const physicalPython = realpath(fsModule, expectedPython);
  if (
    physicalAgentDir !== expectedAgentDir
    || physicalWebuiDir !== expectedWebuiDir
    || physicalPython !== expectedPython
  ) {
    throw new Error("Installed runtime code paths must resolve to their fixed physical paths");
  }
  const pythonMetadata = fsModule.lstatSync(expectedPython);
  if (pythonMetadata.isSymbolicLink() || !pythonMetadata.isFile()) {
    throw new Error("Installed Python entrypoint must be a regular file, not a symlink");
  }
  fsModule.accessSync(expectedPython, (fsModule.constants || fs.constants).X_OK);

  clearInstalledCodeSelectors(runtimeEnv);
  runtimeEnv.PATH = "/usr/bin:/bin:/usr/sbin:/sbin";
  runtimeEnv.TAIJI_AGENT_ROOT = physicalRoot;
  runtimeEnv.TAIJI_AGENT_AGENT_DIR = physicalAgentDir;
  runtimeEnv.TAIJI_AGENT_WEBUI_DIR = physicalWebuiDir;
  runtimeEnv.TAIJI_AGENT_PYTHON = physicalPython;
  runtimeEnv.TAIJI_WEBUI_PYTHON = physicalPython;
  runtimeEnv.TAIJI_WEBUI_AGENT_DIR = physicalAgentDir;
}

function securityProfileDefaults(profileName) {
  if (profileName === "full") return { name: "full", mode: "full", allow: true };
  if (profileName === "local_controlled") {
    return { name: "local_controlled", mode: "restricted", allow: true };
  }
  return { name: "strict", mode: "restricted", allow: false };
}

function applySecurityProfile({
  launchProfile,
  runtimeEnv,
  sourceEnv = runtimeEnv,
  packaged = false,
}) {
  if (launchProfile.kind === INSTALLED_PROFILE) {
    const requestedProfile = String(sourceEnv.TAIJI_SECURITY_PROFILE || "").trim();
    const profileName = requestedProfile === "" || requestedProfile === "local_controlled"
      ? "local_controlled"
      : "strict";
    const delegateEnabled = Object.prototype.hasOwnProperty.call(
      sourceEnv,
      "TAIJI_ALLOW_DELEGATE_TASK",
    ) && /^(1|true|yes|on|y)$/i.test(String(sourceEnv.TAIJI_ALLOW_DELEGATE_TASK).trim());
    const skillScriptsEnabled = Object.prototype.hasOwnProperty.call(
      sourceEnv,
      "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
    ) && /^(1|true|yes|on|y)$/i.test(
      String(sourceEnv.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS).trim(),
    );

    runtimeEnv.TAIJI_SECURITY_PROFILE = profileName;
    runtimeEnv.TAIJI_SECURITY_MODE = "restricted";
    if (profileName === "strict") {
      for (const name of SECURITY_ALLOW_FLAGS) runtimeEnv[name] = "0";
    } else {
      runtimeEnv.TAIJI_ALLOW_TERMINAL = "1";
      runtimeEnv.TAIJI_ALLOW_EXECUTE_CODE = "1";
      runtimeEnv.TAIJI_ALLOW_DELEGATE_TASK = delegateEnabled ? "1" : "0";
      runtimeEnv.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS = skillScriptsEnabled ? "1" : "0";
    }
    return securityProfileDefaults(profileName);
  }

  const explicit = String(sourceEnv.TAIJI_SECURITY_PROFILE || "").trim();
  const profile = ["strict", "local_controlled", "full"].includes(explicit)
    ? securityProfileDefaults(explicit)
    : securityProfileDefaults(packaged ? "strict" : "local_controlled");
  runtimeEnv.TAIJI_SECURITY_PROFILE = sourceEnv.TAIJI_SECURITY_PROFILE || profile.name;
  runtimeEnv.TAIJI_SECURITY_MODE = sourceEnv.TAIJI_SECURITY_MODE || profile.mode;
  if (profile.name === "local_controlled" || profile.name === "full") {
    for (const flag of SECURITY_ALLOW_FLAGS) {
      if (!Object.prototype.hasOwnProperty.call(sourceEnv, flag)) runtimeEnv[flag] = "1";
    }
  }
  return profile;
}

function requiresSourceGate(launchProfile) {
  return launchProfile.kind === "source" && launchProfile.mode === "formal";
}

function allowsDevTools(launchProfile) {
  return launchProfile.kind !== INSTALLED_PROFILE;
}

module.exports = {
  DEFAULT_INSTALL_ROOT,
  INSTALLED_PROFILE,
  RELEASE_MANIFEST_SCHEMA,
  SECURITY_ALLOW_FLAGS,
  allowsDevTools,
  applyInstalledRuntimePaths,
  applySecurityProfile,
  requiresSourceGate,
  resolveLaunchProfile,
  securityProfileDefaults,
};

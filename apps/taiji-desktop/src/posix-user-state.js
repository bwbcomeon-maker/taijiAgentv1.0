const fs = require("node:fs");
const path = require("node:path");

function sameDirectory(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function assertDirectoryShape(metadata, value) {
  if (metadata.isSymbolicLink()) {
    throw new Error(`Taiji user state path must not contain a symbolic link: ${value}`);
  }
  if (!metadata.isDirectory()) {
    throw new Error(`Taiji user state path must contain only directories: ${value}`);
  }
}

function assertOwnedDirectory(metadata, value, userUid) {
  assertDirectoryShape(metadata, value);
  if (metadata.uid !== userUid) {
    throw new Error(`Taiji user state directory is not owned by the current account: ${value}`);
  }
}

function assertTrustedAncestor(metadata, value, userUid) {
  assertDirectoryShape(metadata, value);
  if (metadata.uid === userUid) {
    if ((metadata.mode & 0o002) !== 0) {
      throw new Error(`Taiji user state ancestor is world writable: ${value}`);
    }
    return;
  }
  if (
    metadata.uid === 0
    && (metadata.mode & 0o002) === 0
    && ((metadata.mode & 0o020) === 0 || metadata.gid === 0)
  ) {
    return;
  }
  throw new Error(`Taiji user state ancestor is not trusted: ${value}`);
}

function ensurePrivateUserStateDirectory({
  accountHome,
  stateDir,
  fsModule = fs,
  userUid = typeof process.getuid === "function" ? process.getuid() : null,
}) {
  if (!Number.isInteger(userUid) || userUid < 0) {
    throw new Error("Taiji user state ownership cannot be verified on this platform");
  }
  const home = path.resolve(String(accountHome || ""));
  const target = path.resolve(String(stateDir || ""));
  const relative = path.relative(home, target);
  if (
    !relative
    || relative === ".."
    || relative.startsWith(`..${path.sep}`)
    || path.isAbsolute(relative)
  ) {
    throw new Error(`Taiji user state directory must stay below the system account home: ${target}`);
  }

  const homeMetadata = fsModule.lstatSync(home);
  assertTrustedAncestor(homeMetadata, home, userUid);

  const parts = relative.split(path.sep);
  let current = home;
  for (const [index, part] of parts.entries()) {
    current = path.join(current, part);
    let metadata;
    try {
      metadata = fsModule.lstatSync(current);
    } catch (error) {
      if (!error || error.code !== "ENOENT") throw error;
      fsModule.mkdirSync(current, { mode: 0o700 });
      metadata = fsModule.lstatSync(current);
    }
    const final = index === parts.length - 1;
    if (final) assertOwnedDirectory(metadata, current, userUid);
    else assertTrustedAncestor(metadata, current, userUid);
  }

  const constants = fsModule.constants || fs.constants;
  if (!Number.isInteger(constants.O_DIRECTORY) || !Number.isInteger(constants.O_NOFOLLOW)) {
    throw new Error("Taiji user state directory cannot be opened safely on this platform");
  }
  const descriptor = fsModule.openSync(
    target,
    constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
  );
  try {
    const before = fsModule.fstatSync(descriptor);
    assertOwnedDirectory(before, target, userUid);
    const current = fsModule.lstatSync(target);
    assertOwnedDirectory(current, target, userUid);
    if (!sameDirectory(before, current)) {
      throw new Error(`Taiji user state directory changed while being secured: ${target}`);
    }
    fsModule.fchmodSync(descriptor, 0o700);
    const after = fsModule.fstatSync(descriptor);
    if ((after.mode & 0o777) !== 0o700) {
      throw new Error(`Taiji user state directory permissions could not be secured: ${target}`);
    }
  } finally {
    fsModule.closeSync(descriptor);
  }
  return target;
}

module.exports = { ensurePrivateUserStateDirectory };

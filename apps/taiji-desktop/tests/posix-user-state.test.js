const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { ensurePrivateUserStateDirectory } = require("../src/posix-user-state");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-posix-state-"));
  const accountHome = path.join(root, "home");
  const stateDir = path.join(accountHome, ".local", "state", "taiji-agent");
  fs.mkdirSync(accountHome, { mode: 0o700 });
  return {
    accountHome,
    cleanup: () => fs.rmSync(root, { force: true, recursive: true }),
    stateDir,
  };
}

function mode(value) {
  return fs.statSync(value).mode & 0o777;
}

test("creates the product state directory privately under a permissive umask", () => {
  const sample = fixture();
  const previousUmask = process.umask(0o002);
  try {
    ensurePrivateUserStateDirectory({
      accountHome: sample.accountHome,
      stateDir: sample.stateDir,
    });

    assert.equal(mode(sample.stateDir), 0o700);
  } finally {
    process.umask(previousUmask);
    sample.cleanup();
  }
});

test("repairs an owned 0775 product state directory without deleting contents", () => {
  const sample = fixture();
  try {
    fs.mkdirSync(sample.stateDir, { mode: 0o700, recursive: true });
    const marker = path.join(sample.stateDir, "keep.txt");
    fs.writeFileSync(marker, "keep", { mode: 0o600 });
    fs.chmodSync(sample.stateDir, 0o775);

    ensurePrivateUserStateDirectory({
      accountHome: sample.accountHome,
      stateDir: sample.stateDir,
    });

    assert.equal(mode(sample.stateDir), 0o700);
    assert.equal(fs.readFileSync(marker, "utf8"), "keep");
  } finally {
    sample.cleanup();
  }
});

test("rejects a symbolic-link product state directory", () => {
  const sample = fixture();
  try {
    const parent = path.dirname(sample.stateDir);
    const outside = path.join(path.dirname(sample.accountHome), "outside");
    fs.mkdirSync(parent, { mode: 0o700, recursive: true });
    fs.mkdirSync(outside, { mode: 0o700 });
    fs.symlinkSync(outside, sample.stateDir);

    assert.throws(
      () => ensurePrivateUserStateDirectory({
        accountHome: sample.accountHome,
        stateDir: sample.stateDir,
      }),
      /symbolic link/,
    );
    assert.equal(mode(outside), 0o700);
  } finally {
    sample.cleanup();
  }
});

test("rejects a group-writable ancestor instead of changing its permissions", () => {
  const sample = fixture();
  try {
    const localDir = path.join(sample.accountHome, ".local");
    fs.mkdirSync(localDir, { mode: 0o700 });
    fs.chmodSync(localDir, 0o775);

    assert.throws(
      () => ensurePrivateUserStateDirectory({
        accountHome: sample.accountHome,
        stateDir: sample.stateDir,
      }),
      /ancestor.*writable/,
    );
    assert.equal(mode(localDir), 0o775);
  } finally {
    sample.cleanup();
  }
});

test("accepts secure root-owned ancestors around an owned product directory", () => {
  const accountHome = "/home/customer";
  const stateDir = "/home/customer/.local/state/taiji-agent";
  const rootMetadata = {
    dev: 1,
    gid: 0,
    ino: 1,
    isDirectory: () => true,
    isSymbolicLink: () => false,
    mode: 0o040755,
    uid: 0,
  };
  const targetMetadata = {
    dev: 1,
    gid: 1000,
    ino: 2,
    isDirectory: () => true,
    isSymbolicLink: () => false,
    mode: 0o040775,
    uid: 1000,
  };
  const fsModule = {
    closeSync: () => {},
    constants: fs.constants,
    fchmodSync: (_descriptor, requestedMode) => {
      targetMetadata.mode = 0o040000 | requestedMode;
    },
    fstatSync: () => targetMetadata,
    lstatSync: (value) => value === stateDir ? targetMetadata : rootMetadata,
    mkdirSync: () => assert.fail("all fixture directories already exist"),
    openSync: (value) => {
      assert.equal(value, stateDir);
      return 17;
    },
  };

  ensurePrivateUserStateDirectory({
    accountHome,
    stateDir,
    fsModule,
    userUid: 1000,
  });

  assert.equal(targetMetadata.mode & 0o777, 0o700);
});

test("rejects root-owned ancestors writable by unprivileged accounts", () => {
  const accountHome = "/home/customer";
  const stateDir = "/home/customer/.local/state/taiji-agent";
  for (const [modeBits, groupId] of [[0o777, 0], [0o775, 100]]) {
    const metadata = {
      gid: groupId,
      isDirectory: () => true,
      isSymbolicLink: () => false,
      mode: 0o040000 | modeBits,
      uid: 0,
    };
    assert.throws(
      () => ensurePrivateUserStateDirectory({
        accountHome,
        stateDir,
        fsModule: { lstatSync: () => metadata },
        userUid: 1000,
      }),
      /ancestor is not trusted/,
    );
  }
});

test("rejects the account home's direct parent as an out-of-scope target", () => {
  const sample = fixture();
  try {
    assert.throws(
      () => ensurePrivateUserStateDirectory({
        accountHome: sample.accountHome,
        stateDir: path.dirname(sample.accountHome),
      }),
      /must stay below/,
    );
  } finally {
    sample.cleanup();
  }
});

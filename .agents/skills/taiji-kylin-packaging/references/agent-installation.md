# Installing This Skill in Agent Products

The repository directory is the canonical source. The `.skill` file is a verified Codex convenience bundle, not a universal cross-product plugin.

## Artifact verification

Before any installation:

1. Obtain the Skill from an operator-approved local path, mounted directory, or private artifact store.
2. Obtain its expected SHA256 through a separately trusted project channel.
3. Verify the `.skill` basename, byte size, SHA256, ZIP inventory, and regular-file-only members.
4. Obtain approval to write into the target product's configured Skill directory or invoke its import action.

Private Skills cannot be discovered automatically on another machine. The administrator must supply the approved path or private-store locator and expected digest. Never search user directories, guess a private registry, reuse credentials, or download a similarly named public package.

## Codex

The supported bundle is `taiji-kylin-packaging.skill`. Import it through the current Codex product's approved Skill import mechanism, or place the unpacked source directory in a configured project/personal Skill root. The target directory must be named `taiji-kylin-packaging`; then confirm discovery and run `doctor.py --selftest`.

Installing into a personal/global Skill root changes state outside the repository and requires explicit approval. Repository tests and bundle validation do not prove that an external Codex installation occurred.

## Claude Code, Gemini, Copilot, and other Agents

Use the unpacked `SKILL.md`, `scripts/`, and `references/` only when the product documents a compatible Skill-folder mechanism. Map the directory through that product's approved configuration and run the doctor explicitly. This project does not claim native installation or runtime verification for those products.

If the product has no compatible Skill mechanism, load the folder as a controlled operating guide. Describe it as documentation-assisted operation, not an installed Skill.

## Dependencies

This Skill has no runtime dependency on another private Skill or plugin. It requires Python 3.8+ for doctor/selftest, plus an operator-supplied Taiji repository or frozen build input for real work. Public tools may be installed only after the user approves the exact tool, source, machine impact, and rollback.

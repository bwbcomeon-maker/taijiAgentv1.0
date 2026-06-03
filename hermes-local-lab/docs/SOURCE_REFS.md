# Source References

This monorepo stores the Hermes Agent and Hermes WebUI source trees directly, not as Git submodules. That keeps a normal `git clone` usable without a separate submodule fetch.

| Component | Upstream | Local branch before monorepo import | Local commit imported |
| --- | --- | --- | --- |
| Hermes Agent | https://github.com/NousResearch/hermes-agent.git | `taiji-local-agent` | `2a11a8c69 feat: support lab workspace runtime resolution` |
| Hermes WebUI | https://github.com/nesquena/hermes-webui.git | `taiji-local-webui` | `a5b835c1 feat: add writing workflow and model config` |

The previous nested `.git` directories were moved to `.local-git-metadata/` on this machine before creating the root repository. That directory is ignored and is only a reversible local backup; it is not required for a fresh clone.

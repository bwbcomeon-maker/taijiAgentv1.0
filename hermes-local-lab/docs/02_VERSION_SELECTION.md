# 02_VERSION_SELECTION

执行时间：2026-05-29 17:32-17:38 CST

## Hermes Agent

| 项目 | 结果 |
| --- | --- |
| 仓库名称 | NousResearch/hermes-agent |
| 仓库地址 | https://github.com/NousResearch/hermes-agent |
| 默认分支 | main |
| Release 情况 | 找到 GitHub Releases |
| 最新稳定 release | v2026.5.29 |
| Release 名称 | Hermes Agent v0.15.1 (2026.5.29) -- The Patch Release |
| Release 日期 | 2026-05-29T01:12:15Z |
| 是否 pre-release | false |
| tag | v2026.5.29 |
| tag ref | b9a9551baf95691d64a4ce92d68d6366475a3fd5 |
| 实际源码 commit | e71a2bd11b733f3be7cf99deafde0066c343d462 |
| 本地 checkout | detached HEAD at tag v2026.5.29 |
| 选择理由 | 该 release 是 GitHub Releases 中最新的非 draft、非 pre-release、非 alpha/beta/rc 稳定版本。 |

## Hermes WebUI

| 项目 | 结果 |
| --- | --- |
| 仓库名称 | nesquena/hermes-webui |
| 仓库地址 | https://github.com/nesquena/hermes-webui |
| 默认分支 | master |
| Release 情况 | 找到 GitHub Releases |
| 最新稳定 release | v0.51.157 |
| Release 名称 | v0.51.157 |
| Release 日期 | 2026-05-28T20:09:16Z |
| 是否 pre-release | false |
| tag | v0.51.157 |
| tag ref | 97624626d2c8ed7de18cb21120913bdb40998749 |
| 实际源码 commit | cf003ae98699263aef05a99291daf10aee717809 |
| 本地 checkout | detached HEAD at tag v0.51.157 |
| 选择理由 | 该 release 是 GitHub Releases 中最新的非 draft、非 pre-release、非 alpha/beta/rc 稳定版本。 |

## 兼容性判断

- Hermes WebUI README 说明默认可通过本地 Hermes Agent 源码/venv 运行，也支持 `HERMES_WEBUI_CHAT_BACKEND=gateway` 连接运行中的 Hermes Gateway/API Server。
- 未发现 WebUI release note 或 README 明确要求固定到某个更旧 Hermes Agent tag。
- 因此本阶段按规则分别选择两者最新稳定 release，并记录 tag 与实际 commit。

## 关键确认命令

```bash
curl -sL https://api.github.com/repos/NousResearch/hermes-agent/releases
curl -sL https://api.github.com/repos/nesquena/hermes-webui/releases
git ls-remote --tags --refs https://github.com/NousResearch/hermes-agent.git refs/tags/v2026.5.29
git ls-remote --tags --refs https://github.com/nesquena/hermes-webui.git refs/tags/v0.51.157
```

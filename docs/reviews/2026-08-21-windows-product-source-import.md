# Windows 产品源导入与 Kylin 菜单隔离审计

## 审计结论

本记录仅证明 `codex/cross-platform-package-controller` 功能分支完成了固定 Windows 产品提交的本地导入、逐提交映射、聚焦回归和 Windows 与 Linux/Kylin 菜单配置隔离。本轮未执行 SSH、Windows 制包、Linux DEB 制包、安装、签名或发布，也未执行真实 Electron 桌面验收。

## 固定来源身份

- Import directory: `/Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a`
- Base commit: `5364233e1297e5f2837382823d4e35a0d114aba7`
- Tip commit: `89954e96d23cf43f266197813eb283475d5ff7e1`
- Product import manifest SHA256: `add3b6b13a98354d7be5553673bd2fb07ce8da9840c78b6d82d77e14d2676c97`
- Bundle SHA256: `d8c015b3da586e9012ca7a292e98b42a628e495074f99acbf89d9fabe5cd6f31`
- Archive ref: `refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1`
- Archive ref resolved value: `89954e96d23cf43f266197813eb283475d5ff7e1`
- Trial root: `/private/tmp/taiji-win-product-trial-89954e96-fast`
- Trial final HEAD: `7fb54ee1e6e2d20485f5359e51c3f3963c120b1c`
- Functional branch isolation commit: `e346f506cb36ec581308db67516a07211570ff5e`

`verify` 证明 manifest 包含四个单父提交，allowlist 与实际唯一变更路径均为精确十条。`install-ref` 按无覆盖合同成功安装 archive ref。`inventory` 与 `git rev-parse` 的输出均与上述 base、tip 和 ref 一致。

## 精确十路径

1. `apps/taiji-desktop/src/main.js`
2. `apps/taiji-desktop/src/windows-runtime.js`
3. `apps/taiji-desktop/tests/windows-runtime.test.js`
4. `apps/taiji-desktop/tests/windows-startup-scope.test.js`
5. `hermes-local-lab/config/taiji-default-config.yaml`
6. `hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py`
7. `hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py`
8. `hermes-local-lab/sources/hermes-webui/api/config.py`
9. `hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py`
10. `packaging/windows/diagnose.ps1`

## 四提交映射

| Old SHA | Stable patch-id | Trial SHA | Functional branch SHA | Exact paths |
| --- | --- | --- | --- | --- |
| `8b2fb10bd219695e6643d9d10f764f16e6b47799` | `413b551520f3cc4fa2f32f02a3ab1765654bc416` | `2c6ed22996a1d885b7c14f2341e00beab69deeff` | `e729621e990a25b759b39ba837ad0a4f3785f2e6` | `apps/taiji-desktop/src/main.js`; `apps/taiji-desktop/src/windows-runtime.js`; `apps/taiji-desktop/tests/windows-runtime.test.js`; `hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py`; `hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py`; `packaging/windows/diagnose.ps1` |
| `39f7e908a886effaa1bcba773c84e313ff2bed38` | `7a7aaf50cf9fb6221fba35a200b4130a6eb4a474` | `59c766760d2c8ea994ff90f973e025585a0ff365` | `97c7e6f8d229872c04169f3ab9ee0c387383c8a7` | `apps/taiji-desktop/src/main.js` |
| `a2206deedb029a1cf4fa221b1c794f6900157b1c` | `f0ed69fb356d5102f0487f5f139881c581183b8a` | `dded75851568ddf12526674e7a4d463830c94b5b` | `98809497b7194503b32ea00be8b8d83393ddbd2a` | `apps/taiji-desktop/src/main.js`; `apps/taiji-desktop/tests/windows-startup-scope.test.js` |
| `89954e96d23cf43f266197813eb283475d5ff7e1` | `525c3a69f05f9ff9399e6ec83b0abc099057bfc0` | `7fb54ee1e6e2d20485f5359e51c3f3963c120b1c` | `8eaad194de83a16687f7c44750209a7e38b1ac2d` | `apps/taiji-desktop/src/windows-runtime.js`; `apps/taiji-desktop/tests/windows-runtime.test.js`; `hermes-local-lab/config/taiji-default-config.yaml`; `hermes-local-lab/sources/hermes-webui/api/config.py`; `hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py` |

## Trial 命令与实际结果

- `git clone --no-local --branch codex/cross-platform-package-controller /Users/bwb/Documents/工作/taiji-agentv1.0 /private/tmp/taiji-win-product-trial-89954e96-fast`: PASS。
- 从固定 bundle fetch tip 到 trial archive ref: PASS。
- 按映射表顺序执行四次 `git cherry-pick`: 4 of 4 PASS，无冲突，每次后 porcelain 为空。
- `node --check` 检查 `main.js` 和 `windows-runtime.js`: 2 of 2 PASS。
- Node 测试 `windows-runtime.test.js` 与 `windows-startup-scope.test.js`: 6 of 6 PASS。
- 原计划合并 pytest 命令使用默认 `python3`: FAIL，在收集前报 `No module named pytest`。
- 改用机器已有 Python 3.13 执行合并 pytest 命令: FAIL，Agent 与 WebUI 目录都暴露 `tests.conftest`，pytest 报 `ImportPathMismatch`，业务断言未完整执行。
- 使用机器已有 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` 分开运行 Agent 测试: 3 of 3 PASS。
- 使用既有 Hermes site-packages、trial 的 Agent 绝对路径、既有 Hermes Python 和端口 `29779` 分开运行 WebUI 测试: 25 of 25 PASS。

上述两个合并命令失败均保留为失败记录，没有被分开运行的成功结果改写成 PASS。

## 真实功能分支命令与实际结果

- 四次 cherry-pick 前后的 `git status --porcelain` 均为空。
- 四次 stable patch-id 均与 manifest 一致，四次 cherry-pick 均 PASS，无冲突。
- RED Node 回归: 5 项中 4 PASS、1 FAIL；唯一失败是 Windows 专属菜单配置尚不存在。
- RED Python 回归: 2 项中 1 PASS、1 FAIL；唯一失败是 Windows 专属菜单配置尚不存在。
- GREEN `node --check` 检查 `main.js` 和 `windows-runtime.js`: 2 of 2 PASS。
- GREEN Node 聚焦回归: 6 of 6 PASS。
- GREEN Agent 分开 pytest: 3 of 3 PASS。
- GREEN WebUI 分开 pytest 在沙箱内使用端口 `29781` 和 `29783` 时各自 FAIL，每次均是 25 项 fixture 在业务断言前报本地端口 bind 不可用。
- GREEN WebUI 分开 pytest 在允许本地测试服务监听的受控环境中使用空闲端口 `29785`: 25 of 25 PASS。
- GREEN unittest 运行 `tests.test_windows_menu_policy_isolation` 与 `tests.test_linux_golden_orchestrator`: 38 of 38 PASS，耗时 214.274 秒。
- `git diff --check`: PASS。
- 仅暂存四个规定路径并创建 `e346f506cb36ec581308db67516a07211570ff5e`，subject 为 `fix(packaging): isolate Windows menu policy from Kylin`。
- 提交后 `node --check`: 2 of 2 PASS。
- 提交后 Node 聚焦回归: 6 of 6 PASS。
- 提交后 Agent 分开 pytest: 3 of 3 PASS。
- 提交后 WebUI 分开 pytest 使用真实功能 worktree 的 Agent 绝对路径和空闲端口 `29787`: 25 of 25 PASS。
- 提交后 unittest: 38 of 38 PASS，耗时 221.112 秒。
- 提交后 `git diff --check`: PASS，`git status --porcelain` 为空。
- 审计提交后重复执行 `verify`: BLOCKED，返回 `IMPORT_MANIFEST_EXISTS`。脚本会在验证 bundle 后创建 `product-import.json`，并明确拒绝覆盖已存在的 manifest，因此该命令不是可重复执行的验签命令。本轮未删除或改写 manifest，后续使用 manifest SHA、`inventory`、archive ref 和 stable patch-id 做无覆盖复验。

## Windows 与 Linux/Kylin 菜单隔离

- `packaging/windows/taiji-default-config.yaml` 保留产品提交后的 Windows 菜单配置，其 `profiles` 可见性为 `false`。
- shared `hermes-local-lab/config/taiji-default-config.yaml` 仅将 `profiles` 可见性恢复为 `true`。
- 把 Windows 配置的 `profiles` 规范化为 `true` 后，其完整 YAML 对象与 shared 配置完全相等。
- `apps/taiji-desktop/tests/windows-runtime.test.js` 的菜单 fixture 只读取 Windows 专属配置。
- Linux `99_本机_准备制包输入包.sh`、`00_制包机_生成离线交付包.sh`、`01_制包机_发布预检.sh` 和 `packaging/linux/deb/build-deb.sh` 均不引用 Windows 专属配置。
- `packaging/linux/deb/build-deb.sh` 仍通过 `DEFAULT_CONFIG` 引用 shared `hermes-local-lab/config/taiji-default-config.yaml`。
- 本隔离提交未修改 Linux 99、00、01，未修改 Linux build 脚本，未修改 common core。

## 未验证边界

- Task 5 Lane C 所属的 Windows Stage 专属配置复制尚未实施。
- 真实 Windows 桌面菜单、安装态、断网生命周期、签名和发布未验证。
- 真实 Kylin 制包、安装和桌面验收未验证。
- 真实浏览器、截图、可访问性自动化和视觉回归未执行。

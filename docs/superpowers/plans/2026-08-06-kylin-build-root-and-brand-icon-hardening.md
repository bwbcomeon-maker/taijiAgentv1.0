# Kylin Build Root and Brand Icon Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 修复麒麟制包机因 /tmp 执行限制导致的原生模块测试失败，并把用户批准的蓝色太极机器人贯通标准 DEB、Linux 桌面、Electron 窗口、Web favicon/PWA 与制品审计。

**Architecture:** 制包入口在安装构建依赖后从用户缓存目录和 /var/tmp 选择一个经过“可执行文件运行 + 动态库加载”实测的 owner-only 构建根；所有源码、Node 工具链和子进程临时目录统一继承该根。图标链以用户批准的蓝色机器人为 canonical 产品图形，使用 GPT Image 2.0 生成经视觉核验的小尺寸应用图标源，再确定性派生 Web 与 hicolor PNG，最后由 DEB/AppStream/desktop-id/WM_CLASS 和 payload/native verify 共同验收。

**Tech Stack:** Bash、Python 3 标准库、macOS sips（一次性资产派生）、GPT Image 2.0、Electron 39/Node 22、Debian dpkg-deb/AppStream/hicolor、Python unittest、Node --check、Git archive。

---

## 实施边界与固定接口

本计划继续遵守已确认的第一版范围：x86_64/amd64 + 图形桌面 + dpkg/apt + Kylin/UOS/openKylin。ARM、RPM-only、无图形桌面和 Windows 不进入本轮。标准 DEB 可以通过 AppStream 提高图形安装器展示 Logo 的概率，但不承诺操作系统文件管理器给 .deb 文件本身显示指定图标。

当前分支的实际基线：

~~~text
worktree: /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/linux-sales-grade-installer
branch:   codex/linux-sales-grade-installer
HEAD:     a538c522b6512dbde989875f134419682ac1274c
~~~

每个任务都必须保留现有用户文件边界，不修改 canonical 根目录中的 AGENTS.md 和 操作步骤.docx，也不把 .superpowers/ 评审临时目录加入提交。

## 文件责任图

### 构建兼容

- Modify: taijiagent 打包交付/00_制包机_生成离线交付包.sh：候选构建根、真实执行/动态库探针、统一临时环境、构建工具根和诊断。
- Modify: tests/test_linux_desktop_packaging_static.py：替换硬编码 /tmp 断言，增加动态选择、探针和 TMPDIR/TMP/TEMP 顺序合同。

### Logo/桌面/DEB

- Create: packaging/linux/validate_icon_assets.py：只依赖 Python 标准库，检查 PNG/ICO 格式、尺寸、权限、同源摘要和旧引用。
- Create: tests/test_linux_icon_chain.py：源资产、favicon/PWA、desktop/AppStream、Electron 类名、payload/native verify 静态与二进制合同。
- Create: packaging/linux/taiji-agent.metainfo.xml：标准 AppStream desktop application 元数据。
- Modify: hermes-local-lab/sources/hermes-webui/static/index.html：只引用蓝色机器人 PNG/ICO。
- Modify: hermes-local-lab/sources/hermes-webui/static/manifest.json：PWA icons 全部使用 PNG。
- Modify: hermes-local-lab/sources/hermes-webui/static/sw.js：缓存蓝色 PNG，不再缓存黑金 SVG。
- Modify: hermes-local-lab/sources/hermes-webui/api/routes.py：保留 /favicon.ico 路由，但服务新产品 ICO。
- Modify/Delete: hermes-local-lab/sources/hermes-webui/static/favicon*.svg：运行时不再引用旧黑金 SVG；确认无其它调用后删除旧文件。
- Add/Replace: hermes-local-lab/sources/hermes-webui/static/assets/taiji/logo/logo-mark-icon.png 和 static/favicon-{32,48,64,128,192,256,512}.png：GPT Image 2.0 视觉核验后的派生资产。
- Replace: hermes-local-lab/sources/hermes-webui/static/favicon.ico：由蓝色机器人 PNG 派生的 ICO。
- Modify: packaging/linux/taiji-agent.desktop：StartupWMClass=taiji-agent 和稳定启动信息。
- Modify: packaging/linux/bin/taiji-agent：传入稳定 Electron class 参数。
- Modify: apps/taiji-desktop/src/main.js：设置 desktop name/name、主窗口和登录窗口图标。
- Modify: packaging/linux/deb/build-deb.sh：安装 hicolor 多尺寸图标、AppStream、安装态资源，执行 icon validator 并把摘要写入 manifest。
- Modify: packaging/linux/payload-contract.json：声明 desktop、AppStream、hicolor 和安装态资源路径。
- Modify: hermes-local-lab/scripts/taiji-native-verify：安装后检查产品图标路径、PNG 尺寸和同源摘要。

### 文档与交付证据

- Modify: docs/runbooks/taiji-kylin-uos-offline-delivery.md：记录 /tmp 根因、动态构建根、Logo 能力边界和本轮验证台账。
- Modify: taijiagent 打包交付/操作说明.md：将旧 /tmp 描述改为动态安全工作区和探针行为。
- Modify: taijiagent 打包交付/版本信息.txt：同步制包机日志、工作区和图标验收说明。
- Create: docs/reports/2026-08-06-kylin-icon-chain-ux-qa.md：中文《前端 UX QA 报告》，区分源码、制包机和真实桌面验证状态。

## Task 1: 为受限 /tmp 写构建根回归测试

**Files:**

- Modify: tests/test_linux_desktop_packaging_static.py
- Test helpers: 从脚本文本中提取 build_root_candidates、probe_build_root、select_build_root、configure_build_tmp 函数执行。

- [ ] **Step 1: 先写会失败的静态与行为测试**

增加以下测试名和断言：

~~~text
test_offline_builder_does_not_hardcode_tmp_as_default_build_root
test_offline_builder_checks_exec_and_shared_library_mapping_before_unpack
test_offline_builder_exports_tmpdir_tmp_temp_under_selected_root
test_offline_builder_honors_explicit_root_and_fails_closed_when_probe_fails
test_offline_builder_candidate_order_uses_xdg_cache_home_home_cache_then_var_tmp
test_offline_builder_records_findmnt_and_probe_results_in_failure_diagnostic
test_offline_builder_moves_node_tool_root_under_selected_build_root
~~~

行为夹具用一个临时 shell harness 模拟候选目录：第一个候选的执行探针返回失败，第二个候选成功；断言输出选择第二个路径且导出的三个环境变量都以 selected/tmp 开头。显式 TAIJI_BUILD_ROOT 失败时断言返回非零、没有尝试其它候选。

- [ ] **Step 2: 运行受影响测试确认 RED**

Run:

~~~bash
python3 -m unittest tests.test_linux_desktop_packaging_static.OfflineBuilderStaticTest -v
~~~

Expected：当前测试找不到新函数或发现脚本仍固定 /tmp，至少有一项失败；失败必须来自缺失合同，不得是测试环境异常。

## Task 2: 实现安全构建根选择和统一临时目录

**Files:**

- Modify: taijiagent 打包交付/00_制包机_生成离线交付包.sh

- [ ] **Step 1: 引入未解析的构建根变量和显式覆盖标志**

把顶部固定值改为三态变量：显式设置 TAIJI_BUILD_ROOT 时只验证该值，未设置时 BUILD_ROOT 为空，BUILD_TMP_DIR/TOOL_ROOT/NODE_ROOT 在选择成功后才赋值。保留 SRC_DIR 的逻辑接口，但在选择成功后重新赋值，避免空值在探针阶段被使用。

- [ ] **Step 2: 实现候选列表和安全路径检查**

新增 build_root_candidates() 和 validate_candidate_build_root()。候选按以下顺序生成，使用 Bash 数组和 while IFS= read -r，不能用会拆分空格路径的无引号命令替换：

~~~text
TAIJI_BUILD_ROOT（显式时只验证它）
XDG_CACHE_HOME/taiji-agent-build-<uid>
HOME/.cache/taiji-agent-build-<uid>
/var/tmp/taiji-agent-build-<uid>
~~~

每个候选必须是绝对路径、basename 以 taiji-agent-build- 开头、不是交付目录或其子目录、不是 /tmp、/home、/var 等宽泛目录、不是符号链接；创建后 owner 为当前用户、模式为 0700，所有权 marker 为当前用户 0600。显式路径不合格立即 fail，自动路径才继续下一个候选。

- [ ] **Step 3: 实现真实执行和动态库加载探针**

新增 probe_build_root()，在候选专用 .probe 子目录中运行：

~~~bash
printf '#include <stdlib.h>\nint main(void) { return 0; }\n' > "$probe_dir/probe.c"
cc "$probe_dir/probe.c" -o "$probe_dir/probe-exec"
"$probe_dir/probe-exec"
cc -shared -fPIC "$probe_dir/probe.c" -o "$probe_dir/probe.so"
python3 - "$probe_dir/probe.so" <<'PY'
import ctypes
import sys
ctypes.CDLL(sys.argv[1])
PY
~~~

探针失败时记录 findmnt -T candidate（若命令可用）、stat、失败阶段和原始错误，清理 .probe 后返回失败。不要用 chmod +x、sudo 或跳过探针掩盖系统安全策略。

- [ ] **Step 4: 实现选择、工具根和临时环境配置**

新增 select_build_root() 和 configure_build_tmp()：

~~~bash
BUILD_ROOT="$selected"
SRC_DIR="$BUILD_ROOT/taiji-agentv1.0"
BUILD_TMP_DIR="$BUILD_ROOT/tmp"
TOOL_ROOT="$BUILD_ROOT/.build-tools"
NODE_ROOT="$TOOL_ROOT/node"
mkdir -p "$BUILD_TMP_DIR" "$TOOL_ROOT"
chmod 0700 "$BUILD_TMP_DIR" "$TOOL_ROOT"
export TMPDIR="$BUILD_TMP_DIR" TMP="$BUILD_TMP_DIR" TEMP="$BUILD_TMP_DIR"
~~~

把 select_build_root 放在 install_build_dependencies 成功之后、prepare_source_release 之前；这样 cc、Python 和 findmnt 已可用，而源码解压、uv、Node、npm、resvg 物化和 DOCX 测试都继承安全临时目录。

- [ ] **Step 5: 更新日志快照和失败建议**

write_environment_snapshot 增加 BUILD_TMP_DIR、TOOL_ROOT、TMPDIR/TMP/TEMP、候选探针结果和 findmnt 输出。把旧的“默认使用 /tmp”建议改为“检查新版候选目录诊断”，并在失败信息中明确“不要关闭麒麟安全策略”。

- [ ] **Step 6: 运行构建根回归测试确认 GREEN**

Run:

~~~bash
python3 -m unittest tests.test_linux_desktop_packaging_static.OfflineBuilderStaticTest -v
bash -n 'taijiagent 打包交付/00_制包机_生成离线交付包.sh'
~~~

Expected：新增构建根测试全部 PASS，Bash 语法检查退出码为 0。

- [ ] **Step 7: 提交构建根修复**

~~~bash
git add 'taijiagent 打包交付/00_制包机_生成离线交付包.sh' tests/test_linux_desktop_packaging_static.py
git commit -m 'fix(packaging): select executable Kylin build roots'
~~~

## Task 3: 生成并核验蓝色太极图标资产

**Files:**

- Create: hermes-local-lab/sources/hermes-webui/static/assets/taiji/logo/logo-mark-icon.png
- Replace/Create: hermes-local-lab/sources/hermes-webui/static/favicon-{32,48,64,128,192,256,512}.png
- Replace: hermes-local-lab/sources/hermes-webui/static/favicon.ico
- Create: packaging/linux/validate_icon_assets.py
- Create: tests/test_linux_icon_chain.py

- [ ] **Step 1: 用 GPT Image 2.0 生成小尺寸适配候选**

使用已查看并获用户确认的 logo-mark.png 作为参考，提示词固定为：

~~~text
Edit this existing Taiji Agent blue robot logo into a production Linux app-icon source.
Preserve the exact recognizable blue-and-white robot/swirl mark, proportions, colors and
transparent background. Do not add text, letters, shadows, gradients, frames or new symbols.
Add only safe transparent padding and crisp edges so the mark remains legible at 32 px.
Square 1024x1024 PNG, transparent background, no watermark.
~~~

用 view_image 检查输出：若出现文字、背景、变形、额外符号或偏离 A 的配色，重新生成；通过后保存为 logo-mark-icon.png。该文件是图标链的派生源，现有页面内部使用的 logo-mark.png 仍保留为品牌基准。

- [ ] **Step 2: 在 macOS 生成确定性尺寸和 ICO**

使用系统 sips，不在制包机运行图片生成：

~~~bash
ICON_SOURCE='hermes-local-lab/sources/hermes-webui/static/assets/taiji/logo/logo-mark-icon.png'
for size in 32 48 64 128 192 256 512; do
  sips -z "$size" "$size" "$ICON_SOURCE" \
    --out "hermes-local-lab/sources/hermes-webui/static/favicon-$size.png" >/dev/null
done
sips -s format ico "$ICON_SOURCE" \
  --out hermes-local-lab/sources/hermes-webui/static/favicon.ico >/dev/null
~~~

检查每个 PNG 的 IHDR 尺寸、颜色类型和透明通道；禁止把未查看的生成结果直接复制进制品。

- [ ] **Step 3: 写标准库图标验证器**

validate_icon_assets.py 固定 CLI：

~~~text
validate_icon_assets.py --web-static PATH --install-icons PATH --resource-icon PATH
~~~

验证器用标准库读取 PNG magic/IHDR，检查期望尺寸 32,48,64,128,192,256,512、RGBA/RGB、非符号链接、权限不含 world-write，并检查所有安装态/桌面 PNG 与 favicon-512.png 摘要一致；ICO 必须以 00 00 01 00 开头。输出 JSON 包含 canonical_sha256、sizes、ico_sha256 和 schema: taiji-icon-assets/v1，任何不匹配返回非零。

- [ ] **Step 4: 写图标链失败测试并运行 RED/GREEN**

tests/test_linux_icon_chain.py 至少包含：

~~~text
test_web_static_icon_matrix_has_expected_png_sizes
test_old_black_gold_svg_is_not_referenced_by_html_manifest_or_service_worker
test_favicon_ico_is_a_product_ico_and_not_legacy_bytes
test_desktop_entry_declares_taiji_wm_class_and_icon_name
test_appstream_declares_desktop_id_and_taiji_icon
test_native_verifier_checks_same_source_icon_and_sizes
~~~

Run:

~~~bash
python3 -m unittest tests.test_linux_icon_chain -v
~~~

先确认旧引用测试在修改 Web 文件前能捕获旧 SVG；完成资产和验证器后再次运行，预期全部 PASS。

- [ ] **Step 5: 提交图标资产和验证器**

~~~bash
git add hermes-local-lab/sources/hermes-webui/static packaging/linux/validate_icon_assets.py tests/test_linux_icon_chain.py
git commit -m 'feat(packaging): standardize Taiji Linux icon assets'
~~~

## Task 4: 统一 Web favicon、PWA 和桌面类名

**Files:**

- Modify: hermes-local-lab/sources/hermes-webui/static/index.html
- Modify: hermes-local-lab/sources/hermes-webui/static/manifest.json
- Modify: hermes-local-lab/sources/hermes-webui/static/sw.js
- Modify: hermes-local-lab/sources/hermes-webui/api/routes.py
- Modify: hermes-local-lab/sources/hermes-webui/tests/test_pwa_manifest_sw.py
- Modify: packaging/linux/taiji-agent.desktop
- Modify: packaging/linux/bin/taiji-agent
- Modify: apps/taiji-desktop/src/main.js

- [ ] **Step 1: 替换 Web/PWA 引用**

把 index.html 的 favicon 顺序固定为 PNG：

~~~html
<link rel="icon" type="image/png" sizes="32x32" href="static/favicon-32.png">
<link rel="icon" type="image/png" sizes="512x512" href="static/favicon-512.png">
<link rel="shortcut icon" type="image/x-icon" href="static/favicon.ico">
~~~

把 manifest.json 的 icons 和 shortcut 全部指向 PNG；把 service worker 缓存从 ./static/favicon.svg 改为 ./static/favicon-32.png、./static/favicon-192.png 和 ./static/favicon-512.png。保留 /favicon.ico 路由路径，但确认服务的新 ICO 字节。无其它引用后删除旧 favicon.svg 和 favicon-512.svg，避免运行时请求仍能拿到旧黑金图形。

- [ ] **Step 2: 增加 Linux desktop-id/WM_CLASS 合同**

taiji-agent.desktop 增加：

~~~ini
StartupWMClass=taiji-agent
X-GNOME-WMClass=taiji-agent
~~~

packaging/linux/bin/taiji-agent 将最后启动改为：

~~~bash
exec "$ELECTRON_BIN" --class=taiji-agent "$APP_DIR"
~~~

apps/taiji-desktop/src/main.js 在创建窗口前调用 app.setName("taiji-agent")、app.setDesktopName("taiji-agent.desktop")；主窗口保持 icon，登录窗口也设置 icon: resolveIconPath(resolveLabDir()) || undefined。

- [ ] **Step 3: 运行 Web/desktop 静态与 JS 测试**

~~~bash
python3 -m unittest hermes-local-lab.sources.hermes-webui.tests.test_pwa_manifest_sw -v
python3 -m unittest tests.test_linux_icon_chain -v
node --check apps/taiji-desktop/src/main.js
bash -n packaging/linux/bin/taiji-agent
~~~

Expected：旧 SVG 引用测试转绿，桌面入口和 Electron 源码语法通过。

## Task 5: 把图标链纳入 DEB、AppStream、payload 和 native verify

**Files:**

- Create: packaging/linux/taiji-agent.metainfo.xml
- Modify: packaging/linux/deb/build-deb.sh
- Modify: packaging/linux/payload-contract.json
- Modify: packaging/linux/verify-payload.py（仅在图标尺寸/摘要需要新 version kind 时）
- Modify: hermes-local-lab/scripts/taiji-native-verify
- Modify: tests/test_linux_payload_contract.py
- Modify: tests/test_linux_desktop_packaging_static.py

- [ ] **Step 1: 写 AppStream 文件和失败测试**

AppStream 的固定合同为：

~~~xml
<component type="desktop-application">
  <id>taiji-agent.desktop</id>
  <name>太极 Agent</name>
  <summary>本地智能体工作台</summary>
  <launchable type="desktop-id">taiji-agent.desktop</launchable>
  <icon type="stock">taiji-agent</icon>
</component>
~~~

测试断言 XML 可解析、id 与 desktop-id 一致、stock icon 为 taiji-agent，且不引用网络 URL。

- [ ] **Step 2: 修改 DEB staging**

在 build-deb.sh 中增加 icon source/validator/metainfo 变量；创建 /usr/share/icons/hicolor/size x size/apps、/usr/share/metainfo，从 Web static 安装 32/48/64/128/192/256/512 PNG，把 512 PNG 同时复制到 /opt/taiji-agent/resources/icons/taiji-agent.png，安装 AppStream，并在构建前后运行：

~~~bash
python3 "$ICON_VALIDATOR" \
  --web-static "$SOURCE_WEB_DIR/static" \
  --install-icons "$PKG_ROOT/usr/share/icons/hicolor" \
  --resource-icon "$INSTALL_ROOT/resources/icons/taiji-agent.png"
~~~

扩展 audit_deb_payload required paths、write_package_manifest 的 icon set 摘要和 taiji-package-manifest/v3 的 icon 字段。最终解包后再次运行 validator，确保 DEB 没有在压缩/复制过程中替换图标。

- [ ] **Step 3: 扩展 payload contract 和安装后 native verify**

在 payload-contract.json 声明：

~~~text
usr/share/applications/taiji-agent.desktop
usr/share/metainfo/taiji-agent.metainfo.xml
usr/share/icons/hicolor/32x32/apps/taiji-agent.png
usr/share/icons/hicolor/48x48/apps/taiji-agent.png
usr/share/icons/hicolor/64x64/apps/taiji-agent.png
usr/share/icons/hicolor/128x128/apps/taiji-agent.png
usr/share/icons/hicolor/192x192/apps/taiji-agent.png
usr/share/icons/hicolor/256x256/apps/taiji-agent.png
usr/share/icons/hicolor/512x512/apps/taiji-agent.png
opt/taiji-agent/resources/icons/taiji-agent.png
~~~

hermes-local-lab/scripts/taiji-native-verify 在 installed-production 下调用 validator 的安装态模式，至少检查 512 资源图标与 Web favicon-512.png 同摘要，并输出 [OK] Product icon chain is consistent 或 [FAIL]。

- [ ] **Step 4: 运行 payload/DEB 静态回归**

~~~bash
python3 -m unittest tests.test_linux_payload_contract tests.test_linux_desktop_packaging_static -v
~~~

Expected：完整 fixture 自动包含新增 payload paths；若 contract verifier 需要新 sha256/png kind，先补 verifier 的标准库实现和对应失败测试，再重跑整个 payload suite。

- [ ] **Step 5: 提交 DEB 图标链**

~~~bash
git add packaging/linux/taiji-agent.metainfo.xml packaging/linux/deb/build-deb.sh packaging/linux/payload-contract.json packaging/linux/verify-payload.py hermes-local-lab/scripts/taiji-native-verify tests/test_linux_payload_contract.py tests/test_linux_desktop_packaging_static.py
git commit -m 'feat(packaging): ship branded desktop icon metadata'
~~~

## Task 6: 更新交付手册和中文 UX QA 报告

**Files:**

- Modify: docs/runbooks/taiji-kylin-uos-offline-delivery.md
- Modify: taijiagent 打包交付/操作说明.md
- Modify: taijiagent 打包交付/版本信息.txt
- Create: docs/reports/2026-08-06-kylin-icon-chain-ux-qa.md

- [ ] **Step 1: 固化本次真实错误经验**

在 runbook 故障矩阵新增记录：resvgjs.linux-x64-gnu.node: failed to map segment from shared object + 假 npm EACCES/真实 npm fallback；根因写为“/tmp noexec 或等效策略的高置信结论，精确挂载标志由新版诊断确认”，修复写为候选目录、真实探针和统一临时变量。

- [ ] **Step 2: 更新操作说明中的工作区与 Logo 边界**

把旧的 /tmp/taiji-agent-build-<uid> 描述改为“脚本自动选择用户缓存或 /var/tmp 的可执行 owner-only 工作区”；增加失败时查看 findmnt/探针日志的命令，并说明标准 DEB/AppStream 不能控制 .deb 文件管理器图标。加入安装后检查桌面、任务栏、窗口、favicon/PWA 的验收项。

- [ ] **Step 3: 输出中文前端 UX QA 报告**

报告必须包含：检查范围、资产来源、桌面/AppStream/Web/PWA 链路、已执行命令、源码层结果、未执行的真实 Kylin UKUI/UOS DDE 截图项、P0/P1/P2 风险和目标机验收命令。真实桌面截图在用户运行新版 DEB 前明确标记“未验证”，不能以静态测试替代。

- [ ] **Step 4: 提交文档和 QA 报告**

~~~bash
git add docs/runbooks/taiji-kylin-uos-offline-delivery.md 'taijiagent 打包交付/操作说明.md' 'taijiagent 打包交付/版本信息.txt' docs/reports/2026-08-06-kylin-icon-chain-ux-qa.md
git commit -m 'docs: record Kylin build and icon acceptance gates'
~~~

## Task 7: 全量本地门禁和来源核对

**Files:**

- No new product files; execute tests against the committed worktree.

- [ ] **Step 1: 运行格式、脚本和资产门禁**

~~~bash
git diff --check HEAD~4..HEAD
bash -n 'taijiagent 打包交付/00_制包机_生成离线交付包.sh'
bash -n packaging/linux/bin/taiji-agent
bash -n hermes-local-lab/scripts/taiji-native-verify
node --check apps/taiji-desktop/src/main.js
python3 -m py_compile packaging/linux/validate_icon_assets.py
~~~

- [ ] **Step 2: 运行受影响测试集**

~~~bash
python3 -m unittest \
  tests.test_linux_icon_chain \
  tests.test_linux_payload_contract \
  tests.test_linux_desktop_packaging_static \
  tests.test_deb_maintainer_lifecycle \
  -v
~~~

Expected：退出码为 0；任何平台条件跳过必须在报告中保留原因。

- [ ] **Step 3: 运行完整 Python 回归并记录来源**

~~~bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
~~~

记录 Python 实际路径、worktree、branch、HEAD 和 git status --short。失败必须按根因修复，不能删测试、提高阈值或把稳定失败归类为偶发。

- [ ] **Step 4: 运行前端 UX QA 源码审计**

核对 index.html/manifest/sw.js、desktop/AppStream、主窗口/登录窗口和安装态资源路径；没有真实 Kylin/UOS 桌面截图时，QA 报告保留“未验证”。

## Task 8: 形成新的制包机输入包并交接

**Files:**

- Modify only generated delivery outputs outside Git tracked source: taijiagent 打包交付/SHA256SUMS.txt、taiji-agentv1.0-kylin-build-src-<commit>.tar.gz、taijiagent-制包机输入-<commit>.tar.gz。

- [ ] **Step 1: 从经复验的正式来源运行输入包脚本**

执行前必须满足 lifecycle 约束：成果已按授权进入正式 main，正式入口复验通过，不能从脏 worktree 直接冒充销售制包输入。运行：

~~~bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
bash 'taijiagent 打包交付/99_本机_准备制包输入包.sh'
~~~

Expected：生成 taijiagent-制包机输入-<main-commit>.tar.gz，SHA256SUMS.txt 只含当前源码包；输入包排除 .superpowers/、历史安装产物和 macOS metadata。

- [ ] **Step 2: 解包前静态核验输入包**

~~~bash
tar -tzf taijiagent-制包机输入-<commit>.tar.gz | rg 'favicon|metainfo|validate_icon_assets|00_制包机'
sha256sum taijiagent-制包机输入-<commit>.tar.gz
~~~

Expected：包含新版构建脚本、图标资产、AppStream 和验证器；不包含 .superpowers/、旧 DEB、构建日志或目标机数据。

- [ ] **Step 3: 交付制包机执行命令**

制包机联网时进入 taijiagent 打包交付 执行：

~~~bash
bash ./00_制包机_生成离线交付包.sh
~~~

脚本成功前只报告“制包机正在验证/构建”；只有最终 DEB、manifest、.build-success、图标审计和发布预检都通过，才报告“候选制品已生成”。真实 Kylin/UOS 安装与桌面图标仍需目标机证据。

## 自检清单

- [ ] 规格每一节都有对应任务：构建根、探针、临时变量、图标派生、favicon/PWA、desktop/AppStream/WM_CLASS、payload/native verify、文档和目标机验收。
- [ ] 计划中没有通过关闭安全策略、改挂载或跳过测试解决问题的步骤。
- [ ] 每个修改点都有明确文件、失败测试、通过命令和提交边界。
- [ ] 没有把 macOS 静态结果写成 Linux 制包机或真实国产桌面通过。
- [ ] 未把最终 DEB 生成到 macOS；输入包只从经复验正式 main 生成。
- [ ] 目标机截图、任务栏分组、图形安装器 Logo、真实 Web/PWA 显示在没有实机前保持“未验证”。

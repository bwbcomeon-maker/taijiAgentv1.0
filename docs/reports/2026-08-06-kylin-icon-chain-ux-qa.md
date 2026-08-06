# 太极 Agent Linux 图标链前端 UX QA 报告

日期：2026-08-06
范围：统一 amd64 DEB 的 Web favicon/PWA、Linux 开始菜单入口、Electron 主窗口/登录窗口和安装态图标。

## 结论

源码与制包静态合同已实时通过。蓝色太极机器人作为唯一 canonical 图形，已贯通 RGBA PNG、ICO、PWA、hicolor、AppStream、desktop entry、Electron class/窗口和安装态资源。

“已实时验证”不等于“目标机已验证”：当前 macOS 环境无法证明 Kylin/UOS 文件管理器双击、UKUI/X11/Wayland 窗口归属或桌面图标缓存刷新。

## 已实时验证

- `logo-mark-icon.png` 与 `favicon-32/48/64/128/192/256/512.png` 为 8-bit RGBA，尺寸与文件名一致。
- `favicon-512.png`、DEB 安装态 `resources/icons/taiji-agent.png` 设计为与 canonical 512 图标逐字节一致；ICO 为 PNG-backed Windows icon。
- `index.html` 不再引用旧黑金 SVG；manifest icons 全部为 PNG；service worker 预缓存多尺寸 PNG 和 ICO。
- desktop entry 使用 `Icon=taiji-agent`、`StartupWMClass=taiji-agent`、`X-GNOME-WMClass=taiji-agent`；Electron 启动器传入 `--class=taiji-agent`。
- Electron 主窗口和企业身份登录窗口均设置产品图标；应用 name/desktop name 固定为 `taiji-agent` / `taiji-agent.desktop`。
- DEB 构建前后运行标准库 `packaging/linux/validate_icon_assets.py`，payload contract 声明 AppStream、hicolor 全尺寸和安装态资源；native verify 比较安装态资源图标与 Web 512 图标并检查 RGBA 尺寸。

## 验证命令与结果

```text
bash -n taijiagent 打包交付/00_制包机_生成离线交付包.sh       PASS
bash -n packaging/linux/deb/build-deb.sh                      PASS
bash -n hermes-local-lab/scripts/taiji-native-verify           PASS
node --check apps/taiji-desktop/src/main.js                    PASS
python3 -m py_compile packaging/linux/validate_icon_assets.py   PASS
python3 -m unittest tests.test_linux_icon_chain -v             6/6 PASS
python3 -m unittest tests.test_linux_desktop_packaging_static   98 PASS，1 条环境条件跳过
```

PWA 测试文件在当前 macOS 未安装 pytest；其不依赖运行时的测试方法已手动调用通过，路由集成方法因缺少 PyYAML 未实时执行。该项标记为“未验证”，不能替代制包机 venv 中的完整 pytest。

## 未验证与剩余风险

- 未在 Linux amd64 制包机重新构建当前分支的 DEB；因此没有当前制品 SHA256。
- 未在麒麟、统信或 openKylin 图形桌面执行真实双击安装、首次启动、开始菜单刷新和窗口 WM_CLASS 观察。
- 旧桌面缓存可能继续显示历史图标；目标机验收需注销/刷新桌面缓存后再拍摄证据。
- PWA 浏览器缓存/Service Worker 更新需要真实浏览器重新安装或清理旧 worker 后验收。

## 目标机验收建议

在干净目标机从当前候选 DEB 双击安装后，分别检查开始菜单图标、主窗口标题栏/任务栏图标、登录子窗口图标、Web favicon 和 PWA 安装图标；同时运行安装态 `taiji-native-verify`，保存输出和截图。任何一处仍显示旧图形都应以当前 DEB、manifest 和图标摘要回溯，不通过人工“看起来一样”放行。

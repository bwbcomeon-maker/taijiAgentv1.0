# 太极智囊阶段 2 前端 UX QA 报告

> 日期：2026-09-05
> 范围：阶段 2 的角色任务创建前草稿保存、按任务隔离未上传附件和草稿写入顺序；智囊库导航、目录、卡片和详情页面不在本批。

## 结论

本批已用 Node 24 实际执行前端函数：调用 `createZhinangSession(roleId, catalogVersion, options)` 时，客户端先等待当前任务同 SID 的既有写入，再提交最新可见文本；保存成功后才调用 `/api/session/new`，失败时保持原 SID、文本和附件。debounce、立即保存、切换前保存和 clear 共用按 SID 串行队列，晚到的旧请求不能覆盖最新文本或复活已清空草稿。

未上传的原生 `File` 不再进入草稿 JSON（原生序列化结果是无字节内容的 `{}`）；浏览器按 SID 保存数组副本和原 `File` 引用。成功新建角色任务时只激活新 SID 自己的附件或空附件，切回旧 SID 恢复同一个 `File` 对象，clear 后不会复活。普通跨任务加载和同任务 force refresh 也纳入同一实际函数探针。

该钩子尚未接入用户可见智囊页面，因此本报告不声称 F01–F12 UI 已完成。保存失败的真实浏览器提示、双击按钮状态、键盘操作、响应式布局、200% 缩放、截图、读屏和视觉回归均为**未验证**，由后续 UI 阶段完成。

## 已执行证据

| 检查 | 结果 |
| --- | --- |
| 实际函数与请求顺序 | PASS：固定 Node 24 执行 10 个 VM 场景；被测 `sessions.js` SHA-256 `dc87717ce83c271119c36c4ec03ec43f9be29ea04d2a2cd2b3749b2eaf6e543b`。deferred Promise 控制旧 A 与最新 C；C 等待 A 完成后发送并成为最终服务端草稿，clear 同样排在 A 之后 |
| 失败与恢复 | PASS：旧写入失败不毒化后续显式保存；切换前保存或创建失败保持原 SID、文本、原生 `File`；不同 SID 写入互不阻塞 |
| 附件隔离 | PASS：Node 24 原生 `File` 证明新 SID 为空、旧 A/新 B 往返恢复同一对象、clear 后不复活、目标无草稿不继承旧附件 |
| 请求字段契约 | PASS：四个角色创建字段在同一新建请求；服务端草稿仅保存文本，不把原生 `File` 的 `{}` 当成持久附件 |
| 并发/重复操作 | PASS：既有 `_newSessionInFlight` 复用；服务端同 request ID 的两线程请求返回同一 SID |
| JavaScript 语法 | PASS：`node --check static/sessions.js` |
| 受影响关联回归 | PASS：session index、草稿、并发切换、空草稿恢复、附件、force refresh、新建与模型恢复相关 109 项；另有 1 项未修改 boot.js 的既有精确字符串断言失败，不属于本批行为 |
| 统一本地门禁 | PASS：固定 Node 24、仓库 Agent venv、独立 Hermes 状态与进程级自动端口执行 `scripts/verify.sh --full`，WebUI lint、注册的 952 项及其余 root/Desktop/DOCX/Agent/bootstrap/coexistence 套件全部通过；未启动真实浏览器 |
| 浏览器、截图、可访问性、视觉 | 未验证：本批没有用户可见入口，不启动默认浏览器 |

## 后续 UI 验收入口

阶段 4 接入现有外壳后，按 PRD 的五个视口及 900/901/902、1023/1024/1025 断点复验；覆盖保存失败不丢输入、重复点击只创建一个任务、焦点恢复、普通新建不继承角色、详情关闭与键盘/读屏语义。

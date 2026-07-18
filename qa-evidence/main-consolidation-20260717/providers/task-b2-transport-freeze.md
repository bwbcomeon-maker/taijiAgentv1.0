# Task B2 安全出站传输冻结证据

## 结论边界

- 当前子门禁：安全出站传输模块、transport 契约测试、对抗性测试、named trusted proxy registry 默认配置。
- 当前结论：第三候选 transport 子门禁通过；不代表 B2 消费者接线、真实企业代理、整体发布或正式 `main` 合并完成。
- 正式 `main`：本阶段未修改。

## 冻结快照

以下为修复 AnyIO level-cancellation 清理缺口后的最终哈希；主执行者连续两次读取一致，两名独立只读审查者随后复读一致：

| 文件 | SHA-256 |
| --- | --- |
| `agent/safe_outbound_http.py` | `613c544f78de0df1eca4b218d002b41157f1c27d298d744be59c8f8b359c2ea3` |
| `tests/agent/test_safe_outbound_http_transport.py` | `dbd12c02176df44e30c0fb94d05e0bf93cd9bfc2178a8fd2e17ba743e36255b7` |
| `tests/agent/test_safe_outbound_http_adversarial.py` | `5845242c936fdcbb15ba1c5f3dddde692f18d853503f0bfab55b30d3b916aef1` |
| `hermes-local-lab/config/taiji-default-config.yaml` | `1baa2fc616a4e26ac092967b346106334896aa47dbbce74c3c6493fcf2ebd481` |

## 主执行者实时验证

工作目录：

`hermes-local-lab/sources/hermes-agent`

执行结果：

- 新增的 2 条 AnyIO task-group 取消测试在修复前：2 failed、64 deselected，均复现 `close_entered=True, closed=False`。
- 新增的显式异步 close 测试在修复前：1 failed、66 deselected，复现 checkpoint 后 `closed=False`。
- 新增的 transport pool close 测试在修复前：1 failed、67 deselected，复现连接池进入 close 但未完成。
- `ruff format`：2 个文件保持不变。
- `ruff check`：通过。
- `ty check agent/safe_outbound_http.py`：通过。
- `python -m py_compile`：通过。
- transport 契约：8 passed。
- 对抗性测试：68 passed。
- 合并 transport 门禁：76 passed。
- 与尚未实现的消费者契约合跑：12 failed、76 passed、1 warning。12 个失败全部来自 `tests/agent/test_safe_outbound_http.py`，用于驱动后续凭据、custom image/custom vision、下载器和 WebUI 接线，不属于本传输子门禁的 GREEN。

JUnit 证据：

| 证据 | SHA-256 |
| --- | --- |
| `task-b2-transport-green-agent.xml` | `f79d26a34a0ba69e5078c85a3d07941937cfa3ffd0c7afcce711f40f54e5a2c6` |
| `task-b2-transport-adversarial-green-agent.xml` | `5da4a2c62428f07ef035b7c26e43a20ea331332f01ae4d566e72e5329d7dae75` |
| `task-b2-transport-green-all-agent.xml` | `2db013f21acc0a5a31267279dfc640a6f744e189131b97636f0748773ede386f` |

## 已覆盖的主要对抗面

- HTTPS-only、证书校验、SNI/Host 绑定和实际 TCP/TLS peer 一致性。
- DNS 所有回答预验证、数值 IP 连接、同步/异步取消与资源清理；AnyIO level-cancellation 下的 TLS parent、response iterator、显式异步 close 和 transport pool close 使用 shield 完成真实关闭，并保留原取消传播。
- public/private/trusted proxy 三类显式网络范围，禁止环境代理隐式继承和 direct fallback。
- metadata、link-local、unspecified、multicast、reserved/documentation/benchmark/CGNAT、Fake-IP、IPv4-mapped、旧式 IPv4、scoped IPv6、百分号编码主机永久阻断。
- AWS/GCP metadata IPv4、IPv6 与已知 metadata hostname 阻断。GCP 的 `fd20:ce::254` 依据 Google Cloud 官方 metadata 文档纳入永久阻断清单：
  `https://docs.cloud.google.com/compute/docs/metadata/overview`
- named trusted proxy registry 的严格字段、类型、重复 YAML 键、URL、能力、整库原子语义校验。
- 配置优先级统一为：
  `context override > TAIJI_RUNTIME_HOME > HERMES_CONFIG_PATH > HERMES_HOME/default`。
- `Proxy-Authorization`、URL userinfo、恶意端口、控制字符、非法 DNS label、压缩响应和超长 `Content-Length` fail closed。
- JSON 严格 MIME、identity encoding 和有界读取。

## 独立审查

旧候选审查一：

- 结论：APPROVED。
- 针对修复前哈希。
- 独立复跑：72 passed。
- Ruff、Ty、Py_compile：通过。
- P1：0；P2：0。

旧候选审查二（named trusted proxy registry / transport 范围）：

- 结论：APPROVED。
- 针对修复前哈希。
- 独立复跑：72 passed。
- registry 整库原子校验、配置优先级与 packaged config 保留未知根字段已复核。
- P1：0。

旧候选终审：

- 结论：REJECTED，P1：0，P2：1。
- 发现 AnyIO level-cancellation 下异步清理没有 shield；旧 72 项测试因此是假绿。
- 该问题已按 RED → GREEN 流程修复，并新增 3 个对抗性节点；旧冻结哈希和旧 JUnit 已作废。

第二候选终审：

- 结论：REJECTED，P1：0，P2：1。
- 发现 `_CoreAsyncTransport.aclose()` 的连接池关闭仍未 shield；第二候选 75 项仍是假绿。
- 已新增第 4 个 RED 并最小修复；第二候选哈希和 JUnit 已作废。

第三候选终审：

- 两名独立审查者均给出 APPROVED。
- 两名审查者均复跑 4 个 AnyIO cancellation 节点和完整 76 项门禁。
- 两名审查者均确认冻结哈希一致、P1：0、P2：0。
- shield 内完成真实关闭；返回后的下一个 checkpoint 仍响应原外层取消，没有吞掉取消语义。

## 剩余风险与后续门禁

- 真实企业代理尚未提供，未验证真实代理的远端 DNS 策略、认证/签名策略与“允许公网、拒绝私网/Fake-IP/metadata、绝不直连回退”行为；发布门禁必须补测。
- 上游 Hermes dashboard/CLI 能编辑根配置，必须继续限定为本机运维控制面；若开放给普通或远程非运维用户，应升级为产品边界 P1。
- packaged config 对非空 `trusted_proxy_profiles` 的保留已做静态核对和隔离实测；正式 packaging/release gate 仍应增加持久化回归测试。
- 12 个消费者契约仍为预期 RED，下一阶段必须逐项 GREEN 后再运行 Agent/WebUI 全量回归。

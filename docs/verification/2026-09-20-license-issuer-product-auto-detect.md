# License 签发工具产品自动识别验证

## 来源与范围

基线：main `4f1752f5`，开始时工作树干净且远端一致。任务来自工作区
`wbd-国网空天智能体/2026-09-20-license-issuer-auto-detect-plan.md`：签发工具按
机器码 `required_features` 自动识别太极/国网空天产品并写入正确授权标签，移除
手工"功能包"。签名协议、密钥、机器绑定算法与国网客户机校验不变；独立
Python CLI 未改动。

## 根因（已实时复现）

国网机器码 JSON 已含 `required_features: ["kongtian_agent"]`，但
`normalizeMachineRequest()` 不保留该字段，最终签发采用表单"功能包"默认值
`chat,writing`，声明在签发链路中丢失。RED 测试在旧代码上复现：国网请求签出
`features=["chat","writing"]`（5 失败 + 6 错误）。

## 修改内容

- `issuer-core.js`：规范化保留产品声明（内部字段 `requiredFeatures`，缺失保持缺失）；统一识别函数 `resolveProductLicense`，签发标签只来自已知映射（国网 `kongtian_agent`、太极 `chat,writing`），`options.features` 不再参与；字段缺失（旧太极兼容）与存在但无效（空数组/null/非数组/非字符串成员/未知标签/大小写变体/两产品混合/双字段冲突）严格区分，后者一律拒绝；批量逐份预检查，任一异常整批拒绝并指出出错文件，不产出 ZIP/台账；UTF-8 BOM/CRLF 兼容；签发记录与批量结果含产品标识与识别依据。
- GUI（index.html/renderer.js/main.js/styles.css）：移除可编辑"功能包"，新增只读"授权产品"（待识别/已自动识别/兼容旧版机器码/混合计数）与"已选机器"列表；未选择或识别失败时禁用导出并清空待签发请求；取消选择保留此前有效选择；重置回到待识别；IPC 不再转发表单功能标签；导出成功提示展示产品、短码、期限与输出位置。识别展示数据挂 `productRecognition` 独立字段，不覆盖线协议 `product`。
- 测试：`tests/test_taiji_license_issuer_gui.py` 新增产品自动识别测试类并调整旧契约用例（20→21 项）。

## 过程中发现并修复的缺陷

真实 Electron 实测发现首轮实现把识别结果挂在请求对象 `product` 字段上，覆盖线
协议 `product: "taiji-agent"`，签发时被规范化按"机器码文件产品不匹配"拒绝——
单测未覆盖 main.js 装饰→IPC 序列化→再规范化链路。改为独立字段
`productRecognition` 后修复，并新增回归测试
`test_decorated_request_ipc_roundtrip_keeps_protocol_fields`。

## 验证记录（绑定提交 0664e278）

- 单元/行为：`python -m unittest discover -s tests -p 'test_taiji_license_issuer_gui.py'` 21 项通过（Agent venv Python 3.11.15 + 预置 Node 22.23.1）。覆盖：国网请求文件→真实 RSA 签名→`kongtian_agent`、旧太极回退、重复规范化与 IPC 往返稳定、缺失与无效声明区分、标签去重/顺序/大小写、未知/混合/冲突拒绝、残留 `options.features` 无法覆盖、混合批量逐份正确且坏批次无输出、中文空格路径 + BOM + CRLF、记录含产品标识且不泄露私钥或完整 JWT。
- 全量本地门禁：`scripts/verify.sh` exit=0、`verification: PASS`（预置 Node 22.23.1）。
- 国网互通（对方仓库 `wbd-kongtian-agent` bbd4176a68）：`KONGTIAN_TEST_ISSUER_CORE` 指向本仓库实际 `issuer-core.js`，7 项互通测试通过：导出请求→自动识别签名→国网导入 valid→执行门禁通过、旧太极 chat,writing 被拒 `license_feature_missing`、无效声明拒绝、机器不匹配拒绝、到期拦截、混合批量逐份识别、坏批次整批中止。对方仓库 `tests/product_license/` 全量 137 通过 0 失败（旧签发器固定副本基线不变）。
- 真实 Electron 窗口（隔离 HOME + 临时密钥 + 虚构机器身份，CDP 驱动）：初始"待识别"且导出禁用、无功能包输入；国网单机识别→真实 IPC 签发→输出 JWT `features=["kongtian_agent"]`；有效切无效后清空并禁用；旧太极显示"兼容旧版机器码"；混合 3 台批量显示分产品计数与机器明细，ZIP 内 2×`kongtian_agent` + 1×`chat,writing`；重置回待识别；取消选择保留原状态；920 宽度渲染无破损（Emulation 近似，原生窗口缩放未自动化）；.app 入口 SMOKE 冒烟加载并自动退出。截图与产物存于会话临时目录，不入库。

## 前端 UX QA 报告

受影响路径：签发工具表单与侧栏（唯一界面）。

- 可发现性：产品识别结果位于原"功能包"位置（表单第二行右侧），只读灰底；批量时侧栏新增"已选机器"列表。P1 检查通过：识别能力有明确可见入口与状态。
- 状态沟通：待识别/已自动识别/兼容旧版机器码/识别失败/混合计数五态均有区分文案；失败原因显示在"签发状态"区。
- 已知瑕疵（P3，已缓解）：混合批量的产品汇总文本长于只读输入框宽度时视觉截断，已同步 `title` 悬停提示并在"授权摘要"显示完整文本。
- 键盘/只读语义：`readonly` + `aria-readonly`；导出按钮在无有效请求时 `disabled`（不依赖浏览器表单校验兜底）。
- 未验证：屏幕阅读器朗读、原生文件选择对话框点选动作（原生模态无法 CDP 驱动）、原生窗口手动缩放——均标注"未验证"，不宣称通过。

## 未验证项（不得宣称通过）

- 客户机实机矩阵：国网 macOS 开发版仅完成隔离测试上下文导入验证，真实桌面导入未执行；Windows、国产 Linux 目标机导出→签发→导入→重启保持全链路未验证。
- 新提示文案进入安装版需按对应发布流程另行交付。

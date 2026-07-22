# 专家团 V3 实施计划

## 边界

- 只重构专家团门户、发起任务、聊天内工作台和专家团交付验收。
- 不修改聊天、定时任务、设置等非专家团页面的信息架构。
- 新任务只保留两条企业试点组合：`content-creator-team + work_report`、`deep-research-team + research_report`。
- 历史任务只读，不做隐式迁移。
- 第一版资料只支持 UTF-8 纯文本、TXT、Markdown、CSV、JSON，单份最大 10MB。

## 阶段

1. Image2.0 全流程视觉基线与状态板。
2. 资料来源后端契约、CAS、幂等和安全边界。
3. V3 门户和单一聊天内工作台。
4. 阶段补充、复核、修改、DOCX 自动检查、Office 验收和最终交付。
5. 静态/API/Electron/非专家页面隔离/可访问性与视觉 QA。
6. 本地提交、PR、CI、合并后同步正式 `main` 并复验。

## 完成定义

- 所有用户可感知能力均有可见、可发现、可访问入口。
- 专家团状态完全由服务端 `run.view` 投影驱动，前端不推断业务状态。
- V3 不读写旧 `#writeflowStatusDock`，不依赖 `_activeExpertTeamStatusCard`。
- 真实 Electron 主路径、常见宽度和非专家页面回归有证据。
- 最终 DOCX 必须经过自动校验；WPS/Word 实看无法执行时明确标记未验证。

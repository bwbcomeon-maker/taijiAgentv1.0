---
name: style-modeler
description: Use when the user asks to model, learn, extract, compare, or reuse a writing style for articles in the Hermes Writing Agent workflow.
version: 0.6.0-hermes.1
author: xue1127/writing-agent, ported for Hermes Local Lab
license: MIT
metadata:
  hermes:
    tags: [writing, style, voice, articles, writeflow]
    related_skills: [workflow-producer, web-article-extractor]
    source_repo: https://github.com/xue1127/writing-agent
    source_commit: a296f1cdfb88887b336f8dd2776257c18acab99d
---

# Style Modeler for Hermes Writing Agent

## Overview

Build reusable style files for the Writing Agent workflow. This is a
Hermes-native adaptation of the upstream `风格建模` skill.

Default style library:

```text
articles/_styles/
```

Bundled upstream examples are available in `references/styles/`. Treat them as
templates, not as the user's own voice.

## When to Use

Use this skill when the user asks to:

- 学习这个风格
- 提取风格
- 模仿某个作者
- 建立风格库
- 把一篇或多篇文章变成写作配方
- 在 `/writeflow` 项目中套用固定口吻

## Process

1. **Collect samples.**
   - Inline text: analyze directly.
   - Workspace files: read the provided files.
   - URLs: use `web-article-extractor` or Hermes `web_extract`/`browser_*`
     tools to obtain article text first.

2. **Archive source text.**
   Save extracted source material under:

   ```text
   articles/_source/YYYY-MM-DD-title.md
   ```

3. **Check existing styles.**
   Look for matching files in `articles/_styles/`. If a style exists, update it
   instead of creating a near-duplicate.

4. **Analyze the style across these dimensions.**
   - author/persona
   - worldview and reasoning pattern
   - opening and ending patterns
   - paragraph transitions
   - sentence rhythm and punctuation
   - vocabulary fingerprint
   - rhetorical devices
   - formatting habits
   - signature moves
   - anti-AI features
   - taboo words and sentence forms

5. **Write or update a style file.**
   Save to:

   ```text
   articles/_styles/[style-name].md
   ```

## Output Template

```markdown
# 风格名称：[name]

## 01. 作者画像与核心人格

## 02. 思维内核与论证逻辑

## 03. 创作路径还原

## 04. 互动设计

## 05. 词汇指纹

## 06. 句式与标点

## 07. 开头与结尾配方

## 08. 过渡与连接

## 09. 修辞手法

## 10. 格式与排版

## 11. 招牌动作

## 12. 反AI特征

## 13. 段落模板库

## 14. 禁忌清单

## 15. 写作执行提示
```

## Integration With Workflow Producer

When a `/writeflow` project names a style, the workflow should read the chosen
style file and pass it in the `context` for `writing-executor`, `editor-review`,
and `humanizer` stages.

Do not store personal style files inside the skill directory. They belong in
the workspace so the user can inspect, edit, and version them with the article
project.

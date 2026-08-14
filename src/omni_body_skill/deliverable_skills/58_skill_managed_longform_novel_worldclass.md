# 世界顶尖级受管长篇小说工程

## 适用边界

只用于整本、多章、长期续写或明确要求持久化世界设定与大纲的小说工程。临时单章、短篇或仅润色正文，使用“网文章节交付”Skill，不升级为受管工程。

## 不可违反的工作区规则

1. 目标目录已经存在时，第一步必须调用 `novel.project.status`，不得先创建项目或凭文件名猜进度。
2. 状态可恢复时先调用 `novel.project.recover`；规划不完整时只补缺失部分，再 `novel.blueprint.compile`。
3. 目录不存在或不是受管小说工程时，才调用 `novel.project.create`，随后完成整本蓝图，不把“本次只写一章”误当作全书章节数。
4. 正文只能经 `novel.chapter.checkout` → 模型写作 → `novel.chapter.submit` 事务提交；不得用通用 `file.write` 直接写入受管工程的 `正文/`。
5. 世界、人物、冲突、全书大纲、细纲、章节卡和追踪状态必须投影到工程文件夹；`.novel-system` 是权威状态，不能删改为普通草稿目录。

## 执行流程

### 1. 发现与恢复

- 调用 `novel.project.status`。
- 若工程存在：读取 `planning_complete`、`missing_sections`、`resume_action`、`next_chapter` 和最新提交状态。
- 若有中断事务：调用 `novel.project.recover`，然后重新查询状态。
- 若工程不存在：调用 `novel.project.create`，参数中的 `planned_chapters` 是全书规划章节数。

### 2. 完整规划

- 用 `novel.blueprint.update`、`novel.blueprint.patch` 或 `novel.blueprint.upsert_many` 建立故事、世界、人物、地点、时间、冲突事件和全书章节规划。
- 调用 `novel.blueprint.assist` 查缺补漏；时间线或初始位置冲突用确定性修复动作解决。
- 编译前必须做四项确定性预检：人物 `birth_tick` 与日历 `ticks_per_year` 使用同一刻度；事件只列出当时实际在场人物；跨地点移动先建立可达路线或拆成连续事件；调用 `novel.timeline.normalize` 时必须填写本次归一化的 `reason`。
- 编译失败时按返回问题分组修复，优先批量 `upsert_many`，不要反复重建已经通过的世界、人物和章节规划。
- 只有 `novel.blueprint.compile` 通过后才能开始正文事务。

### 3. 续写章节

- 用 `novel.context.query` 取本章所需的权威人物、事件、伏笔和情感上下文，不重复加载整个历史。
- 调用 `novel.chapter.checkout` 获得带状态哈希的章节租约。
- 模型按章节卡写正文，需要时先 `novel.scene.design`。
- 用 `novel.chapter.submit` 提交正文与事实增量；失败则按返回问题修稿后重新提交，不绕过事务。

### 4. 上下文压缩与断点

- 接近运行上下文预算时，允许宿主自动压缩已完成过程；压缩后必须重新调用 `novel.project.status`，再用 `novel.context.query` 恢复当前章节的最小充分上下文。
- 只依赖工程权威状态、最新断点和已提交事实续跑，不依赖被压缩掉的工具回显。
- 已完成阶段只保留关键决策、事实增量、失败原因摘要和最终结果；原始工具噪声不进入后续小说权重。

### 5. 审核与交付

- 调用 `novel.project.audit` 和 `novel.project.status`。
- 最终回复必须同时给出：完整工程文件夹、`正文/` 文件夹、最新已提交章节、下一章编号、规划完整性与断点状态。
- 不把单个正文文件冒充完整交付，也不额外创建与权威状态脱节的正文副本。

## 完成标准

- 工程能被下一次任务直接 `novel.project.status` 加载。
- 完整规划已编译，章节提交是原子事务，审计无阻断错误。
- 工作区可见世界设定、人物设定、冲突网络、全书大纲、细纲、章节卡、正文和追踪数据。
- 最终交付工程文件夹与正文位置，明确下一续写断点。

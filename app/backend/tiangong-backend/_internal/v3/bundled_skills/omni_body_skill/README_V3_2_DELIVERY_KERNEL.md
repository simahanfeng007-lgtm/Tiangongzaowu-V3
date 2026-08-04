# Tiangong Omni Body v3.2 Delivery Kernel

v3.2 不是继续堆应用 action，而是把“能完成”升级成“可交付”。

## 新增能力
- 5 个标杆世界级交付 Skill
- 8 个 QC 质量门
- 模板库
- Rubric 评分
- Preview 预览
- Repair 返工计划
- Deliverable Package 打包

## 核心流程
```text
模板/大纲 → 生成交付物 → QC质检 → 返工计划 → 修改 → 再质检 → 打包
```

## 仍然保持工具定位
- 只注册一个 v3 tool：`omni_body`
- 大模型必须明确传 `action / target / args`
- 工具不做自主规划
- 工具返回 `ok / zhuangtai / evidence`

## 标杆 Skill
- 商业级 Word 方案交付
- 商业汇报 PPT 交付
- 代码工程交付
- 资料/论文综述交付
- 短视频交付

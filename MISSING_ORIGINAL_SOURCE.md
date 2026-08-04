# 来源与重建边界

早期快照曾声明主后端、生命核心和通信服务只剩冻结运行时。当前版本已根据现有三份归档、冻结接口契约、测试语义和运行行为重建并恢复为可读源码，包括：

- `app/backend/tiangong-backend/v3/` 主后端；
- `app/life-service/` 与 `src/life_service/` 生命服务；
- `src/communication_service/` 通信服务；
- `src/total_gateway/` 总网关；
- `app/backend/tiangong-backend/tiangong_kernel/` 基础内核；
- `app/backend/tiangong-backend/v3/fact_kernel/` 事实执行内核；
- Omni Body、Skill、前端和发布流水线。

仍无法证明的内容只有：未提供的历史 Git 提交记录、原作者机器上可能存在但未进入任何归档的文件，以及官方签名/冻结后的原生 EXE。为避免伪造，源码清单明确标记 `production_claim=false`；正式 EXE 必须由当前源码在目标平台重新构建。

# 本地制片控制内核实施计划

> **给执行代理：** 使用测试驱动开发逐项实现；当前会话连续执行，不把书面规格当成新的审批门。

**目标：** 为 `cinematic-director` 增加可由 Codex 调用的本地制片控制内核，覆盖计划导入、任务领取、候选回填、四级审批、失效传播、恢复和交付导出。

**架构：** Python 领域服务管理 SQLite 执行账本，媒体采用内容寻址文件存储，`project-state.json` 保持创作连续性的唯一真源。CLI 只做参数解析和 JSON 输出，所有规则由可测试的服务层执行。

**技术栈：** Python 3 标准库、SQLite、JSON、`unittest`。

## 全局约束

- 所有文档、注释和用户可见错误使用中文。
- 不增加第三方运行时依赖，不连接外部模型 API。
- 每项行为先观察失败测试，再做最小实现。
- 四个审批门不可通过状态文件手工绕过。
- 保留现有 `project-state.json` 与检查器兼容性。

### 任务 1：建立项目、计划和任务图

**文件：**
- 新建：`scripts/production_control/__init__.py`
- 新建：`scripts/production_control/errors.py`
- 新建：`scripts/production_control/store.py`
- 新建：`scripts/production_control/service.py`
- 新建：`tests/test_production_control_project.py`

- [x] RED：初始化项目、重复初始化、非法计划引用、循环依赖和合法计划导入测试。
- [x] GREEN：实现数据库迁移、项目元数据、事件账本和计划校验。
- [x] REFACTOR：集中事务与 JSON 序列化，不扩展未测试功能。

### 任务 2：实现审批门和任务状态机

**文件：**
- 修改：`scripts/production_control/service.py`
- 新建：`tests/test_production_control_gates.py`

- [x] RED：未通过 G1 不能领取资产任务；证据不全不能审批；审批后只解锁对应区间。
- [x] GREEN：实现门顺序、证据任务、状态刷新和不可变审批记录。
- [x] RED：上游接受版本变化使门失效并递归标记下游任务 `stale`。
- [x] GREEN：实现输入血缘和失效传播。

### 任务 3：实现 Codex 领取与结果回填

**文件：**
- 修改：`scripts/production_control/service.py`
- 新建：`scripts/production_control/media.py`
- 新建：`tests/test_production_control_runs.py`

- [x] RED：原子领取、任务包字段、租约、候选多版本、过期输入拒绝测试。
- [x] GREEN：实现 `next_task`、输入快照、内容寻址导入和 `submit_candidate`。
- [x] RED：候选选择、拒绝、重试及历史保留测试。
- [x] GREEN：实现评审与新的运行尝试。

### 任务 4：实现恢复、状态与导出

**文件：**
- 修改：`scripts/production_control/service.py`
- 新建：`tests/test_production_control_recovery_export.py`

- [x] RED：过期租约恢复、非过期租约保留和事件可追溯测试。
- [x] GREEN：实现 `recover` 和状态聚合。
- [x] RED：审批门未齐、存在 `stale` 或未决任务时拒绝最终导出。
- [x] GREEN：在服务层生成交付清单、资产血缘、审批记录和连续性摘要。

### 任务 5：提供稳定 CLI

**文件：**
- 新建：`scripts/production_control_cli.py`
- 新建：`tests/test_production_control_cli.py`

- [x] RED：`init`、`plan`、`next`、`submit`、`review`、`approve-gate`、`status`、`recover`、`export` 的退出码和 JSON 输出测试。
- [x] GREEN：实现薄 CLI，业务错误统一输出机器可读错误码。
- [x] 验证：用临时目录完成一次模拟项目，从初始化运行到最终导出。

### 任务 6：接入导演技能和资产模板

**文件：**
- 修改：`SKILL.md`
- 修改：`README.md`
- 新建：`assets/production-plan-template.json`
- 新建：`references/production-control-plane.md`
- 修改：`evals/evals.json`

- [x] RED：增加技能行为评测，证明旧说明无法执行任务领取和结果回填闭环。
- [x] GREEN：加入“制片控制模式”路由、计划合同、命令协议和人工审批职责。
- [x] 验证：模板通过计划校验，技能快速校验器通过。

### 任务 7：独立复核与交付

**文件：**
- 更新：`CHANGELOG.md`
- 同步：`outputs/cinematic-director/`
- 生成：`outputs/cinematic-director.zip`

- [x] 运行全量单元测试和端到端模拟。
- [x] 运行路径逃逸、SQL 外键、并发领取和审批绕过安全测试。
- [x] 运行官方 `quick_validate.py`、JSON/YAML 解析、Markdown 链接检查和安全扫描。
- [x] 使用独立代码复核代理检查正确性、安全性和缺失测试；修复后复验。
- [x] 同步交付镜像并核对源目录、镜像和压缩包关键文件哈希。

## 完成定义

- Codex 能仅靠 JSON 命令输出完成领取、调用、回填和审片循环。
- 所有审批门、失效、恢复和最终导出均有失败测试与通过证据。
- 不需要供应商凭据即可运行完整模拟项目。
- 第一版未实现项明确保留为 B 阶段，不在代码中留下假接口或 `TODO`。

## 执行写回

- **Result：** 已形成可由 Codex 调用的本地制片控制内核，覆盖输入登记、任务图、领取与租约、候选回填、评审、四级人工审批、失效传播、恢复和交付导出。
- **Conclusion：** 分级自动化可在不接入外部模型 API 的前提下闭环运行；四个方向门仍由用户决定，`--human-confirmed` 是审计声明而非身份认证。
- **Evidence：** 源目录全量 `unittest` 为 78/78；独立代码复审最终 PASS；技能安全扫描 0 项发现；官方快速校验通过。
- **Artifact：** `scripts/production_control_cli.py`、`scripts/production_control/`、`assets/production-plan-template.json`、`references/production-control-plane.md`。
- **Adjustment：** 复审后补强间接失效传播、旧候选拒绝、提交竞态、严格计划校验、人工确认，以及基于目录 FD、`O_NOFOLLOW` 和原子发布的内容寻址存储。
- **Verification：** 源目录与交付镜像均为 78/78 测试通过；压缩包完整性通过，三处关键文件 SHA-256 与源目录一致。

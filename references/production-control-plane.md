# 本地制片控制内核

## 用途

这个控制内核把导演技能产生的创作决策变成可以持续执行的制片任务。它不替代导演判断，也不直接绑定某个模型；它保证每一次模型调用都有明确输入、输出合同、版本、评审、审批门和恢复点。

## 三个唯一真源

- **执行账本**：`.production/production.db` 保存任务、运行、租约、候选、评审、审批门和追加式事件。
- **创作连续性**：`project-state.json` 保存人物、场景、道具、视觉宪法与已验收镜头的观察状态。
- **媒体内容**：`media/objects/` 按 SHA-256 保存实际文件；数据库只登记哈希、类型和用途。

不要把连续性字段复制进数据库，也不要把任务状态写回 `project-state.json`。媒体文件不得写进 SQLite 或 Git。

## 人工审批门

1. **视觉宪法**：锁定画幅、焦段族、摄影支撑、颜色角色、光线逻辑、表演与剪辑规则。
2. **核心资产**：锁定主要角色、核心场景和关键道具的接受版本及压力测试证据。
3. **样片镜头**：锁定代表性工作流、模型能力边界、成本区间和可修复阈值。
4. **画面锁定**：锁定粗剪镜头版本和未决问题清单，之后才允许最终导出。

Codex 可以在门内做技术 QC、选择满足已批准标准的候选并发起重试，但不能代替用户批准四个方向门。用户拒绝时，重做对应证据任务；新版本接受后，内核自动使相关审批和下游结果失效。

## Codex 操作循环

### 1. 初始化与输入登记

```bash
python3 scripts/production_control_cli.py init <project> --title <title> --project-id <id>
python3 scripts/production_control_cli.py ingest <project> <script> --input-id script --role screenplay
```

`ingest` 复制内容并登记哈希，不执行输入文件。符号链接会被拒绝。

### 2. 编译并导入计划

根据 `assets/production-plan-template.json` 生成项目专用计划。长片应把示例中的 `shot-batch` 拆成序列、场景和逐镜任务，使每个视频任务只承担一个主要动作与一个主要摄影机行为。

```bash
python3 scripts/production_control_cli.py plan <project> <production-plan.json>
```

内核检查 ID 唯一、引用存在、任务图无环、四个审批门顺序固定，以及每个门的证据确实由前一审批门解锁。

### 3. 领取、调用和回填

```bash
python3 scripts/production_control_cli.py next <project> --executor codex
```

读取返回的 `task.kind`、`inputs`、`references`、`output_contract`、`input_hash` 和 `lease_until`。根据能力而非品牌选择模型，调用后回填：

```bash
python3 scripts/production_control_cli.py submit <project> \
  --run-id <id> \
  --artifact <file> \
  --metadata '{"model":"模型与版本","prompt":"实际提示词","seed":42,"parameters":{}}'
```

提交时会重新计算输入快照。上游候选、登记输入或连续性状态变化后，旧运行不能写回。

### 4. 评审和重试

```bash
python3 scripts/production_control_cli.py review <project> --candidate-id <id> --decision approve --reviewer codex
python3 scripts/production_control_cli.py review <project> --candidate-id <id> --decision reject --reviewer codex --notes <reason>
python3 scripts/production_control_cli.py retry <project> <task-id> --reason <reason>
```

`approve` 产生新的接受版本，不覆盖历史。上游接受版本变化会递归标记已解锁或已完成下游为 `stale`。

### 5. 用户审批

到门后向用户展示：接受候选、关键差异、QC 证据、已知风险、预计成本和下一阶段影响。只有用户明确批准后才执行：

```bash
python3 scripts/production_control_cli.py approve-gate <project> <gate> --reviewer <human> --notes <decision> --human-confirmed
```

`--human-confirmed` 是“用户已经明确批准”的审计声明，不是身份认证或多人权限系统。Codex 只能在当前对话取得用户对该审批门的明确决定后携带此参数，不能根据评分或候选状态自行设置。

### 6. 恢复与导出

```bash
python3 scripts/production_control_cli.py status <project>
python3 scripts/production_control_cli.py recover <project>
python3 scripts/production_control_cli.py export <project>
```

`recover` 只释放过期租约并保留运行历史。`export` 要求四门全部通过、所有任务完成、没有 `stale` 结果，随后生成 `exports/delivery-manifest.json`。

## 状态解释

| 状态 | 含义 | 下一步 |
|---|---|---|
| `blocked` | 依赖或审批门未满足 | 完成前置任务或等待用户审批 |
| `ready` | 可领取 | `next` |
| `leased` | 执行器正在处理 | 调用模型并 `submit`，或等待租约过期后恢复 |
| `submitted` | 候选待评审 | `review` |
| `completed` | 已有接受候选 | 推进依赖任务或审批门 |
| `stale` | 上游版本已变化 | 检查影响后 `retry` |

## 一致性操作

- 领取视频任务前运行 `validate_project_state.py`，并使用最新接受镜头的 `observed_end`。
- 角色、场景、道具联系表必须作为依赖候选进入任务包，不能只靠提示词重述。
- 局部分镜、色调或拍摄手法必须继承视觉宪法；破例要记录理由、范围和恢复点。
- 同一任务连续失败时先按成本阶梯修复；连续 10–15 次仍失败则改变镜头设计，而不是继续堆提示词。
- 每次接受、重试、审批和恢复后读取 `status`，以数据库实际状态决定下一步。

## 当前边界

第一版由 Codex 调用模型，不保存供应商密钥，不自动付费，不提供 Web UI 或多人权限。后续界面必须复用同一状态机和数据库合同，不能另建项目状态。

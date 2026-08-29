# 自动视觉导演与长片一致性实施计划

> **给执行代理：** 必须逐项使用测试驱动开发；当前会话采用内联执行，不分派实现任务。

**目标：** 为 `cinematic-director` 增加剧情驱动的自动视觉决策、项目级视觉宪法、成片状态回写和一致性验证。

**架构：** 语言模型按结构化证据进行导演判断，确定性 Python 检查器只负责状态契约和漂移约束。`project-state.json` 是唯一机器真相，Markdown/YAML 产物均从该状态继承或与其核对。

**技术栈：** Markdown、YAML、JSON、Python 3 标准库、`unittest`。

## 全局约束

- 所有新增说明、注释和文档使用中文。
- 不增加第三方运行时依赖，不调用外部 API。
- 默认输出唯一方案；仅在低置信度或突破项目视觉宪法时升级。
- 已验收成片状态高于计划状态。
- 保留现有模式、资产和参考资料的兼容性。

---

### 任务 1：定义项目状态检查契约

**文件：**
- 新建：`tests/test_validate_project_state.py`
- 新建：`scripts/validate_project_state.py`
- 新建：`assets/project-state-template.json`

**接口：**
- 输入：一个 UTF-8 JSON 项目状态文件路径。
- 输出：`validate_project_state(state) -> list[Issue]`；CLI 以 JSON 输出问题。
- 退出码：`0` 无错误，`1` 存在错误，`2` 文件或 JSON 无法读取。

- [ ] 先写并运行失败测试：缺少视觉宪法、无恢复点的破例、相邻镜头状态断裂、未授权核心风格漂移均须报错。
- [ ] 实现最小检查器并让测试通过。
- [ ] 增加合法长片状态样例，确认无错误且 CLI 退出码为 `0`。

### 任务 2：定义剧情到视觉的编译协议

**文件：**
- 新建：`references/story-to-visual-compiler.md`
- 修改：`assets/beat-sheet-template.md`
- 修改：`assets/shot-plan-template.md`

**接口：**
- 输入：导演读法的十一项证据。
- 输出：首选场景原型、置信度、三条决定性证据和完整视觉策略。

- [ ] 从基线输出记录“按类型默认”“无置信度”“无证据链”等失败。
- [ ] 写原型评分、唯一方案和低置信度升级规则。
- [ ] 在节拍与分镜模板加入导演证据、继承来源和覆盖理由字段。

### 任务 3：定义长片视觉宪法与状态回写

**文件：**
- 新建：`references/film-consistency-system.md`
- 新建：`assets/film-visual-constitution.yaml`
- 修改：`references/continuity-bible.md`
- 修改：`assets/director-book-template.md`

**接口：**
- 视觉宪法分为 `immutable_core`、`arc`、`exception_budget`。
- 场景合同记录继承值、弧位置、局部覆盖和恢复点。
- 已验收镜头用 `observed_end` 更新后继镜头实际起点。

- [ ] 写全片五段视觉弧和颜色角色规则。
- [ ] 写计划状态与观察状态的合并优先级。
- [ ] 写受控破例预算及跨场漂移检查协议。

### 任务 4：接入主 skill 路由

**文件：**
- 修改：`SKILL.md`
- 新建：`agents/openai.yaml`

**接口：**
- 未指定导演的叙事任务自动加载两个新增参考文件。
- 多场景主流程在原型路由前建立视觉宪法，单场任务在分镜前完成轻量原型路由，验收后回写状态。

- [ ] 修正前置描述为合法且可发现的 YAML。
- [ ] 把原有“导演风格高于一切”改为“戏剧证据与视觉宪法优先”。
- [ ] 增加唯一方案、置信度和破例协议。
- [ ] 生成与 `SKILL.md` 一致的 Codex 界面元数据。

### 任务 5：行为评测与发布验证

**文件：**
- 修改：`evals/evals.json`
- 生成：发布目录与压缩包。

- [ ] 增加现实主义短场景自动选择评测。
- [ ] 增加 90 分钟六段视觉弧评测。
- [ ] 增加多导演偏好冲突评测。
- [ ] 运行单元测试、状态模板验证、官方 `quick_validate.py`、JSON/YAML 解析、Markdown 链接检查和安全扫描。
- [ ] 将必要文件导出到 `outputs/cinematic-director/`，生成 `outputs/cinematic-director.zip`，不包含 `.git` 与开发文档。

## 自检

- 规格中的自动路由、长片一致性、受控破例、成片回写和冲突处理均有对应任务。
- 无 `TBD`、`TODO` 或未定义接口。
- 文件职责互不重叠：判断协议在参考文档，状态约束在脚本，主路由在 `SKILL.md`。

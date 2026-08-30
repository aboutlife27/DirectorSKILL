# Huobao 长篇小说继承实施计划

> **给执行代理：** REQUIRED SUB-SKILL: 使用 `superpowers:test-driven-development` 逐项实现；当前会话连续执行，不把书面计划当成新的审批门。步骤使用复选框跟踪。

**目标：** 建成可验证、可幂等重跑的 Huobao 长篇小说导入器，把《十日终焉》完整继承到仓库外的私有制片项目，并交付第一章 5 个 5 秒试片镜头。

**架构：** 导入器以只读 SQLite 连接和 backup API 固定源视图，只导出显式白名单字段；章节正文进入内容寻址快照，Huobao 派生资料进入候选层。公开仓库保存通用代码、测试和协议，私有项目保存正文、候选资产和制片控制账本。

**技术栈：** Python 3 标准库、SQLite、JSON、SHA-256、`unittest`。

**规格：** `docs/superpowers/specs/2026-08-30-huobao-novel-ingestion-design.md`

## 全局约束

- 所有文档、注释和用户可见错误使用中文。
- 不增加第三方运行时依赖，不连接外部模型 API。
- 小说正文、Huobao 数据库、私有媒体和服务密钥不得进入公开 Git。
- `episode_number` 是章节顺序和稳定 ID 的依据，原始标题只作元数据。
- 同一语料幂等重跑，失败不得改变已发布快照或 `current.json`。
- L2 Huobao 资产统一标记为 `candidate`，不得自动进入 `project-state.json`。

---

### 任务 1：建立章节快照与失败合同

**文件：**
- 新建：`scripts/import_huobao_novel.py`
- 新建：`tests/test_import_huobao_novel.py`

**接口：**
- 输入：`import_project(db_path: Path, drama_id: int, output: Path, expected_title: str | None) -> dict`
- 输出：包含 `status`、`corpus_sha256`、`chapter_count`、`snapshot_path` 和 `candidate_counts` 的结果字典。
- 错误：`ImportFailure(code: str, stage: str, message: str)`；CLI 输出不含正文的 JSON 错误。

- [x] **步骤 1：写正常导入和结构失败测试**

```python
def test_imports_ordered_chapters_and_manifest(self):
    result = importer.import_project(self.db, 2, self.output, "测试剧")
    self.assertEqual(result["chapter_count"], 3)
    self.assertEqual(result["status"], "created")
    self.assertEqual(
        json.loads((self.output / result["manifest_path"]).read_text())["sequence"],
        {"min": 1, "max": 3, "continuous": True},
    )

def test_rejects_empty_duplicate_and_gapped_chapters(self):
    for fixture, code in (("empty", "empty_chapter"), ("duplicate", "duplicate_episode_number"), ("gap", "episode_gap")):
        with self.subTest(fixture=fixture):
            with self.assertRaisesRegex(importer.ImportFailure, code):
                importer.import_project(self.fixture(fixture), 2, self.output / fixture, "测试剧")
```

- [x] **步骤 2：运行测试并观察 RED**

运行：`python3 -m unittest tests.test_import_huobao_novel -v`

预期：因 `scripts/import_huobao_novel.py` 尚不存在而失败。

- [x] **步骤 3：实现最小只读导入和稳定哈希**

```python
class ImportFailure(RuntimeError):
    def __init__(self, code, stage, message):
        self.code, self.stage = code, stage
        super().__init__(message)

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def chapter_digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

使用 `file:<quoted-path>?mode=ro` 打开源库，复制到临时 SQLite 后验证必需表和字段、剧目标题、正文非空、序号唯一且连续；在输出目录的临时兄弟目录写入章节、合订本、清单和验证报告，重新读取校验后再发布。

- [x] **步骤 4：运行目标测试并观察 GREEN**

运行：`python3 -m unittest tests.test_import_huobao_novel -v`

预期：正常导入和所有结构失败合同通过。

### 任务 2：导出候选资产并保证幂等、原子与敏感隔离

**文件：**
- 修改：`scripts/import_huobao_novel.py`
- 修改：`tests/test_import_huobao_novel.py`

**接口：**
- 产物：`imports/huobao/<corpus_sha256>/<candidate_set_sha256>/{characters,scenes,props,existing-scripts,existing-storyboards}.json`
- 每条记录：白名单业务字段及 `source_table`、`source_id`、`source_drama_id`、`source_episode_id`、`import_status`、`record_sha256`。
- 验证：`verify_project(output: Path) -> dict`，不需要源数据库。

- [x] **步骤 1：写候选、幂等、失败原子性和敏感隔离测试**

```python
def test_exports_only_candidate_whitelists(self):
    result = importer.import_project(self.db, 2, self.output, "测试剧")
    records = json.loads((self.output / result["candidate_files"]["characters"]).read_text())
    self.assertEqual(records[0]["import_status"], "candidate")
    self.assertNotIn("local_path", records[0])
    self.assertNotIn("voice_provider", records[0])

def test_same_corpus_is_unchanged_and_failure_preserves_current(self):
    first = importer.import_project(self.db, 2, self.output, "测试剧")
    second = importer.import_project(self.db, 2, self.output, "测试剧")
    self.assertEqual(second["status"], "unchanged")
    self.assertEqual(first["corpus_sha256"], second["corpus_sha256"])
```

同时加入：注入敏感表后不导出、无效 JSON 保留原字符串并标记、模拟发布失败后旧 `current.json` 不变、`verify_project` 检出文件篡改。

- [x] **步骤 2：运行新增测试并观察 RED**

运行：`python3 -m unittest tests.test_import_huobao_novel -v`

预期：候选导出、幂等或验证能力尚不存在而失败。

- [x] **步骤 3：实现字段白名单、记录哈希与验证模式**

```python
CANDIDATE_EXPORTS = {
    "characters": ("characters", ("name", "role", "description", "appearance", "personality", "voice_style", "bio", "traits", "anchor_front", "anchor_side", "anchor_full", "anchor_prompt", "visual_spec", "voice_spec", "behavior_spec", "consistency_anchors", "image_prompt", "anchor_details", "scope", "source_episode_id", "sort_order")),
    "scenes": ("scenes", ("episode_id", "location", "time", "prompt", "storyboard_count", "status", "master_prompt", "visual_spec", "consistency_anchors", "image_prompt", "anchor_details", "scope", "source_episode_id")),
    "props": ("props", ("name", "type", "description", "prompt", "significance", "visual_spec", "consistency_anchors", "image_prompt", "anchor_details", "scope", "source_episode_id")),
}
```

只查询规格列出的六类业务表；排除路径、URL、供应商、时间戳、删除标记和所有配置表。JSON 型字符串仅在合法时增加规范化值，否则保留原字符串并附 `parse_errors`。

- [x] **步骤 4：运行导入器测试并观察 GREEN**

运行：`python3 -m unittest tests.test_import_huobao_novel -v`

预期：全部通过，失败注入后旧快照仍可验证。

### 任务 3：提供 CLI 和导演技能工作流入口

**文件：**
- 修改：`scripts/import_huobao_novel.py`
- 新建：`references/source-ingestion-workflow.md`
- 修改：`SKILL.md`
- 修改：`README.md`
- 修改：`tests/test_feature_workflow_assets.py`
- 修改：`CHANGELOG.md`

**接口：**
- CLI 导入：`python3 scripts/import_huobao_novel.py --db DB --drama-id ID --output PROJECT [--expected-title TITLE]`
- CLI 验证：`python3 scripts/import_huobao_novel.py --output PROJECT --verify-only`
- 成功退出码 `0`；业务校验失败 `1`；参数或系统输入失败 `2`；stdout 始终是一行 JSON。

- [x] **步骤 1：写 CLI 和技能路由失败测试**

```python
def test_cli_outputs_machine_readable_error(self):
    result = subprocess.run([sys.executable, str(CLI), "--db", str(self.db), "--drama-id", "999", "--output", str(self.output)], capture_output=True, text=True)
    payload = json.loads(result.stdout)
    self.assertEqual(result.returncode, 1)
    self.assertEqual(payload["error"]["code"], "drama_not_found")
```

资产测试断言 `SKILL.md` 会在继承小说、旧项目迁移和来源导入请求时加载 `references/source-ingestion-workflow.md`。

- [x] **步骤 2：运行相关测试并观察 RED**

运行：`python3 -m unittest tests.test_import_huobao_novel tests.test_feature_workflow_assets -v`

- [x] **步骤 3：实现薄 CLI 和工作流说明**

CLI 只解析参数、调用 `import_project` 或 `verify_project`、序列化稳定结果；工作流说明写清 L0–L3、命令、候选晋升规则、版权与密钥隔离、制片登记方式和验收清单。

- [x] **步骤 4：运行相关测试并观察 GREEN**

运行：`python3 -m unittest tests.test_import_huobao_novel tests.test_feature_workflow_assets -v`

### 任务 4：执行《十日终焉》私有全量继承、第一章五镜试片与制片登记

**文件：**
- 生成但不提交：`/Users/apple/Documents/Codex/2026-08-11/jin/work/ten-day-terminus-production/`
- 新建但不提交：`/Users/apple/Documents/Codex/2026-08-11/jin/work/ten-day-terminus-production/.gitignore`

**接口：**
- 源：`/Users/apple/Projects/huobao-drama/data/huobao_drama.db`，`drama_id=2`，标题 `十日终焉`。
- 项目：ID `ten-day-terminus`，标题 `十日终焉 AI 影像改编实验`。

- [x] **步骤 1：建立私有项目隔离规则**

```gitignore
source/private/
.production/
media/
exports/
*.db
.env*
```

- [x] **步骤 2：运行真实导入并重复验证**

运行：

```bash
python3 scripts/import_huobao_novel.py --db /Users/apple/Projects/huobao-drama/data/huobao_drama.db --drama-id 2 --output /Users/apple/Documents/Codex/2026-08-11/jin/work/ten-day-terminus-production --expected-title 十日终焉
python3 scripts/import_huobao_novel.py --output /Users/apple/Documents/Codex/2026-08-11/jin/work/ten-day-terminus-production --verify-only
```

预期：1384 章、序号 1–1384、空章和重复均为 0，候选数为角色 121、场景 142、道具 9、已有剧本 1、已有分镜 28。

- [x] **步骤 3：从源库独立抽验首章、中间章和末章**

使用只读 SQLite 查询 episode number `1`、`692`、`1384`，以 `openssl dgst -sha256` 或 Python 标准库计算正文哈希，与清单逐项一致。

- [x] **步骤 4：生成第一章 5×5 秒镜头包**

仅引用第一章正文，不沿用包含后续剧情信息的旧候选分镜。输出统一的视觉锚点、五个独立生成提示、镜头终点、声音建议和拼接顺序，默认按 16:9、24 fps、中文界面的通用免费视频工具编写。

- [x] **步骤 5：初始化制片控制并登记八项输入**

登记 `compiled-novel.md`、来源 `novel-manifest.json`、五类候选 JSON 和第一章镜头包，输入 ID 分别为 `source-novel`、`source-manifest`、`huobao-characters`、`huobao-scenes`、`huobao-props`、`huobao-existing-scripts`、`huobao-existing-storyboards`、`chapter-0001-25s-shot-package`；角色均为来源或候选资产，不导入生产任务图。

### 任务 5：独立复核、全量验证与公开交付

**文件：**
- 复核：本计划涉及的全部公开仓库变更
- 同步：`/Users/apple/.codex/skills/cinematic-director/`
- 同步：`/Users/apple/Documents/Codex/2026-08-11/jin/outputs/cinematic-director/`

- [x] **步骤 1：运行独立代码复核并修复高、中风险发现**

复核重点：SQLite 只读保证、SQL 标识符白名单、路径逃逸、符号链接、原子发布、哈希定义、错误信息泄密和真实源数据兼容性。

- [x] **步骤 2：运行全量自动验证**

```bash
python3 -m unittest discover -s tests -v
python3 scripts/import_huobao_novel.py --output /Users/apple/Documents/Codex/2026-08-11/jin/work/ten-day-terminus-production --verify-only
git diff --check
git grep -n -I -E '十日终焉.{200}|sk-[A-Za-z0-9_-]{16,}|api[_-]?key[[:space:]]*=' -- ':!docs/superpowers/specs/*' || true
```

- [x] **步骤 3：核对公开提交边界并同步安装**

确认 `git status --short` 只包含通用代码、测试、规格、计划和文档；同步安装目录后，在安装副本再次运行导入器 `--help` 与现有技能快速验证。

- [ ] **步骤 4：提交并推送到个人仓库**

```bash
git add SKILL.md README.md CHANGELOG.md scripts/import_huobao_novel.py tests/test_import_huobao_novel.py tests/test_feature_workflow_assets.py references/source-ingestion-workflow.md docs/superpowers/specs/2026-08-30-huobao-novel-ingestion-design.md docs/superpowers/plans/2026-08-30-huobao-novel-ingestion.md
git commit -m "feat: add verifiable Huobao novel ingestion"
git push origin main
```

## 完成定义

- 通用导入器在合成数据库上覆盖成功、失败、幂等、原子性、隔离和离线验证合同。
- 《十日终焉》1384 章与五类候选资产均完成本地私有导入，抽样正文哈希与源库一致。
- 八项输入已进入同一个制片控制项目，内容哈希与导入清单一致。
- 第一章镜头包恰含 5 个 5 秒镜头，五条生成提示逐字继承同一视觉锚点。
- 公开提交不包含小说正文、源数据库、私有媒体、服务密钥或源项目脚本。
- 源仓库、安装副本与交付镜像通过验证，通用能力已推送到 `aboutlife27/DirectorSKILL`。

## 执行写回

- **Result：** 完成通用 Huobao 导入器、私有全量继承、第一章 `5×5` 秒镜头包和八项制片输入登记。
- **Conclusion：** 长篇原著可以作为制片系统的可验证底座；现阶段采用分级自动化，所有 Huobao 派生资产先进入候选层，未经过人工门禁不得成为生产真源。
- **Evidence：** 私有导入共 1384 章，章节范围 1–1384；候选资产为角色 121、场景 142、道具 9、已有剧本 1、已有分镜 28；全量测试 `93/93` 通过。
- **Artifact：** 通用代码位于 `scripts/import_huobao_novel.py`；私有试片包位于 `development/episode-0001/25s-test/shot-package.md`；安装副本和交付镜像已同步。
- **Adjustment：** 独立复核后补强路径逃逸、符号链接、候选内容校验、数据库句柄关闭、错误信息脱敏和 `current.json` 原子切换，并为同一语料的候选变化引入版本化状态。
- **Verification：** 真实项目 `--verify-only` 通过；首章、中间章、末章正文哈希与只读源库一致；五个提示共享同一视觉锚点；交付压缩包完整可解压。

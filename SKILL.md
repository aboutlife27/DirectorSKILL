---
name: cinematic-director
description: >-
  Use when directing or managing production for narrative film, short film, long-form AI film, commercial or video work from a script, prose, brief, storyboard, image, clip, or generation failure; especially for automatic shot selection, cinematography, blocking, color scripts, camera movement, storyboards, prompts, editing, sound, continuity, visual consistency, director's books, one-click production, staged automation, or multi-scene project state.
---

# Cinematic Director Skill

## Operating stance

Act as a working director and previs supervisor, not a critic and not a prompt decorator. The job is to turn source material into a plan someone could actually execute — with a camera crew or with a generation queue.

Three domains have to meet in every answer:

- **Directing** — story, subtext, blocking, staging, shot rationale, performance, visual language.
- **Pre-production** — breakdown, beat sheet, director's book, shot list, coverage, continuity, budget of effort.
- **AI production** — keyframes, reference assets, motion prompts, first/last-frame workflows, tool control surfaces, generation QC and repair.

The enemy is adjective soup. `cinematic, dramatic, masterpiece, 8K` tells a model nothing about where the camera is, what the body does, where the action ends, or what must not change. Replace it with observable physical description and explicit constraints.

## Non-goals

- Do not write film criticism unless asked for critique.
- Do not generate shot variety for its own sake. Every shot answers: what does the audience now understand or feel that they could not one second ago?
- Do not overload one AI video prompt with unrelated actions.
- Do not reinvent identity, costume, era, or location when the user has supplied reference assets.
- Do not reproduce specific shots, dialogue, characters, or plot beats from real films. Style modules teach methods.

## Routing — pick the mode, then load only what you need

`SKILL.md` alone is enough for a short answer. Load reference files on demand, and only the ones the request actually needs. Reading everything wastes context and dilutes the answer.

| The user asks for | Mode | Load |
|---|---|---|
| "What is this scene really about?" / interpretation | A | — |
| Beats, structure, "break this into beats" | B | `assets/beat-sheet-template.md` |
| Visual treatment, director's book, "set the rules" | C | `assets/director-book-template.md`, `references/lighting-and-color.md`, `references/genre-playbooks.md` |
| Shot list, storyboard plan, shooting table | D | `assets/shot-plan-template.md`, `references/cinematic-language.md`, `references/blocking-and-staging.md` |
| Image/keyframe/storyboard-panel prompts | E | `assets/keyframe-prompt-template.md`, `references/image-model-adapters.md` |
| Video motion prompts from existing images | F | `assets/video-prompt-template.md`, `references/prompt-lexicon.md`, `references/ai-video-tool-adapters.md` |
| Recurring characters/locations across shots | G | `references/continuity-bible.md` |
| Sound design, music, dialogue, voice-over | H | `assets/sound-plan-template.md`, `references/sound-and-dialogue.md` |
| "I have clips — how do I cut them together?" | I | `assets/edit-timeline-template.md`, `references/editing-and-assembly.md` |
| "It failed / looks like a slideshow / face changed" | J | `references/failure-modes.md` — for a single named symptom read only its symptom-to-code index and that one code, then stop |
| "…and give me a fixed prompt" | J→F | add `assets/video-prompt-template.md` and the matching tool adapter |
| "Score this / gate this batch / how do I report a failure" | J | `assets/qc-checklist.md` |
| "Direct this" / "everything I need" / full production pass | A–J | run the pipeline in order, loading per step |
| Automatically choose shots, color, lens, or camera from a story | A–D | `references/story-to-visual-compiler.md` |
| Feature film, multi-scene work, or "keep one film consistent" | C–G | `references/film-consistency-system.md`, `assets/film-visual-constitution.yaml`, `assets/project-state-template.json` |
| 审计《Hell Grind》公开工作流、核对其证据或解释哪些做法可迁移 | any | `references/hell-grind-workflow.md` |
| 执行 AI 长片端到端生产、建立 11 阶段门或运行项目看板 | A–J | `references/ai-feature-production-playbook.md`, `assets/ai-feature-production-runbook.yaml`, `references/film-consistency-system.md`, `assets/project-state-template.json` |
| “一键制片”、制片管理、分级自动化、由 Codex 调用模型并持续执行 | K | `references/production-control-plane.md`, `assets/production-plan-template.json`, `references/ai-feature-production-playbook.md` |
| Explain or extend the directing methods and research basis | any | `references/method-provenance.md` |
| "In the style of \<director\>" | any | `references/director_styles/NN_<slug>.md` (exactly one) |
| Names a specific model or platform | any | `references/ai-video-tool-adapters.md` or `references/image-model-adapters.md` |
| Horror / comedy / commercial / vertical / any genre framing | any | `references/genre-playbooks.md` |
| Product, packshot, cosmetics, food, macro | any | `references/product-and-macro.md` |
| Word-level prompt help, negatives, EN↔中文 terms | any | `references/prompt-lexicon.md` |
| Scheduling, retries, versioning, handoff, "how do I run this" | any | `references/production-workflow.md` |

Modes combine, and Mode J is rarely terminal: a repair that lands on re-planning the shot produces new shots, so chain J→F for the prompts, →I for the cut, and →G once more than two shots share invariants.

A full narrative pass runs the steps in order: 1 intake · 2 director read (A) · 3 beats (B) · 4 film constitution · 5 automatic visual decision · 6 optional style reference · 7 director's book (C) · 8 blocking · 9 shots (D) · 10 keyframes (E) · 11 adapter · 12 prompts (F) · 13 sound (H) · 14 edit (I) · 15 QC and state write-back (J), with G running underneath. Steps 4, 5, 11, and 15 are the commonly skipped control points — do not skip them on multi-scene work.

长片端到端执行时，上述叙事流程必须嵌入 `references/ai-feature-production-playbook.md` 的阶段门：先用证据与容量实验锁定交付，再建立资产和一致性正史；之后按首镜证明、单变量生成、并行剪辑、画面锁定、清理、调色和验收闭环推进。阶段 0-5 是集中生成窗口的前置门，两周生成日历从阶段 6 开始；不要把生成窗口误写为完整制作周期，也不要把前期工作重新塞进这两周。

## 制片控制模式（K）

当用户要真正运行项目，而不是只要一份导演方案时，使用本地控制内核。A–J 生成创作决策和模型输入，K 保存任务图、版本、候选、评审、审批门、恢复点和交付血缘。第一版由 Codex 领取任务包并调用当前可用模型；不要假装内核已经直连供应商，也不要把未验证链路升级为无人值守。

固定闭环如下：

1. 从 `assets/production-plan-template.json` 复制并按剧本编译项目任务图。
2. 初始化项目并登记剧本、视觉参考和已有资产。
3. 导入计划后领取一个任务包；根据任务的 `kind`、输入引用和输出合同选择模型。
4. 调用模型后回填候选文件及 `model`、`prompt`、`seed` 和关键参数。
5. 在同一审批门区间内可自动做技术 QC、拒绝和重试；每次主要只改一个变量。
6. 到视觉宪法、核心资产、样片镜头或画面锁定时停止，把证据与候选交给用户。Codex 不能代替用户批准这四个门。
7. 每次修改后读取 `status`；中断后先 `recover`，最终交付前运行 `export`。

命令合同（所有成功与业务错误均输出 JSON）：

```bash
python3 scripts/production_control_cli.py init <project> --title <title> --project-id <id>
python3 scripts/production_control_cli.py ingest <project> <source> --input-id script --role screenplay
python3 scripts/production_control_cli.py plan <project> <production-plan.json>
python3 scripts/production_control_cli.py next <project> --executor codex
python3 scripts/production_control_cli.py submit <project> --run-id <id> --artifact <file> --metadata '<json>'
python3 scripts/production_control_cli.py review <project> --candidate-id <id> --decision approve --reviewer codex
python3 scripts/production_control_cli.py approve-gate <project> <gate> --reviewer <human> --human-confirmed
python3 scripts/production_control_cli.py status <project>
python3 scripts/production_control_cli.py recover <project>
python3 scripts/production_control_cli.py export <project>
```

`review approve` 表示候选进入当前接受版本，可由 Codex 按已批准标准执行；`approve-gate` 是项目方向锁定，必须有用户明确决定。`--human-confirmed` 只是记录该决定的审计声明，不是身份认证，Codex 不得自行添加。用户在门上要求修改时，使用 `retry` 重做证据任务；新版本被接受后，内核会使已批准门和下游结果失效。详细状态、输入输出与故障恢复规则见 `references/production-control-plane.md`。

### Response size

Over-production is the most common failure of this skill. Match the answer to the ask.

| Request | Ceiling |
|---|---|
| One prompt for one shot | The prompt, at most three lines of rationale, one tool-specific rule |
| A symptom with no artifact attached | Diagnosis, the fix ladder, at most one question |
| A one-line brief | Assumptions in one line, then at most one screen of deliverable |
| A named mode | That mode's deliverable only — do not volunteer the neighbouring modes |
| A script, a multi-scene piece, or "everything I need" | The full pipeline |

## Intake and defaults

Extract or infer this. If information is missing, proceed on stated assumptions — do not interrogate the user. Ask at most one question, and only when a wrong guess would make the whole deliverable useless (for example, aspect ratio for a vertical-only campaign, or whether an era is period or modern).

```yaml
project:
  title: optional
  format: feature | short film | trailer | social vertical | scene | commercial | music video | documentary | unknown
  target_duration: seconds
  aspect_ratio: 16:9 | 9:16 | 2.39:1 | 1.85:1 | 4:3 | 1:1 | unknown
  target_tool: Runway | Veo | Kling | Luma | Sora-class | Hailuo | Pika | Vidu | Jimeng/Dreamina/Seedance | Wanxiang | other | unknown
  genre: horror | thriller | drama | comedy | action | sci-fi | noir | romance | commercial | documentary | ...
  director_style: spielberg | hitchcock | kubrick | kurosawa | scorsese | fellini | bergman | tarkovsky |
                  wong_kar_wai | nolan | villeneuve | fincher | refn | bi_gan | zhang_yimou |
                  hou_hsiao_hsien | park_chan_wook | malick | michael_mann | coen_brothers | none
source:
  text: script | prose | brief | logline
  core_conflict: inferred
  emotional_arc: inferred
assets:
  characters: reference images/videos if provided
  locations: reference images/videos if provided
  props: reference images/videos if provided
  audio: voice/music/reference if provided
constraints:
  era: e.g. Republican-era China, modern Toronto
  must_keep: identity, costume, lighting, setting, props, palette
  must_avoid: modern objects, text, watermark, extra characters, face change
output:
  deliverables: [A..J]
  language: match the user's language
```

This block is a checklist for you. Never print it back to the user.

Defaults when unspecified:

- **Output language** — match the user's. **Generation prompt language** — English by default. If the user writes in Chinese and names no tool, assume a Chinese-UI tool: write the prompts in Chinese, keep camera and lighting terms in their standard Chinese trade forms (`推镜`, `侧逆光`, `景深`), and state the assumption in one line. Give both languages only when the user names an English-first tool or asks for a mirror.
- **Aspect** — 16:9 for narrative, 9:16 if the user says social/短视频/Reels/抖音.
- **Clip length** — 3–5s for character performance, 3–4s for fine hand work, 8–12s for environmental or product motion with no legible face. Longer only when the model holds narrative stably and the shot earns it. Duration strategy and its mechanism are owned by `references/ai-video-tool-adapters.md`.
- **Camera** — locked or one slow push-in when continuity is fragile. Complex moves only when the story needs them and the tool controls them.
- **Motion budget** — one primary subject action + one camera behavior + one environmental motion per clip. See the motion-budget model in `references/ai-video-tool-adapters.md`.
- **Register** — derive it from dramatic evidence inside the project visual constitution. Genre and director references may suggest methods, but neither outranks the story or the film's established rules.

## Hard rules

1. **Story function first.** A shot with no story function is deleted, not improved.
2. **Blocking before framing.** Decide what bodies do in space, then place the camera. A well-blocked scene reads even shot flat.
3. **One dominant camera move per clip.** None is a legitimate choice and often the better one.
4. **Every action has an end state.** "He turns" is not a shot. "He turns until his profile is against the window, then stops" is.
5. **Behavior, not emotion.** Never prompt "he is afraid." Prompt what fear looks like on a body.
6. **Continuity is engineered.** Name the invariants — identity, wardrobe, light direction, era, geography — and repeat them verbatim across every prompt in a scene.
7. **The keyframe is the control point.** A video model mostly re-animates what the still already decided. Fix the still before spending video generations.
8. **Match the tool's control surface.** Do not ask a model for a feature it does not expose. Identify the surface, then pick the prompt shape.
9. **Negatives are targeted, and they name instances.** Include only the constraints matching this shot's real risk. "No modern objects" is a category the model cannot resolve — name the things: no plastic, no lever handles, no rubber soles, no wristwatch, no printed logos. Long negative lists dilute and can invoke what they forbid.
10. **One project truth.** Multi-scene work has one `project-state.json`. Accepted footage writes the next real start state; rejected footage never becomes an ancestor, an old plan never overrules visible fact, and a scene boundary changes state only through a reasoned `state_resets` entry.
11. **Methods, not imitation.** Translate director names into functional methods, then keep only those supported by dramatic evidence and the visual constitution. Never mix complete style packages.
12. **One answer by default.** Return the highest-confidence visual strategy. Ask for a choice only when the top two candidates differ by less than 10 points or a choice would break the visual constitution.
13. **Answer the size of the question.** A prompt request gets a prompt. Never expand a small ask into a production package, and never ask for information you can reasonably assume — state the assumption instead.

## Pipeline

Fifteen steps. Run only the ones the request needs, in this order. Each step names the file to load if you go deep.

### 1. Intake and scope

Fix the deliverable, duration, aspect, tool, genre, and constraints. State assumptions in one or two lines, then work. Decide the shot budget: target duration ÷ average shot length for the register gives the shot count (`references/editing-and-assembly.md`).

### 2. Director read and subtext breakdown

Identify, in this order:

- **Literal event** — what happens on the surface.
- **Dramatic question** — what the audience should be wondering.
- **Conflict** — external and internal.
- **Subtext** — what the scene is actually about.
- **Emotional movement** — opening emotion → pressure → turn → closing emotion.
- **Visual thesis** — the simplest visual idea that governs the scene.

For narrative work, also record the turn, point of view, audience information advantage, power in/out, objective, obstacle, tactic, pressure delta, and visible suppressed behavior. Use the exact contract in `references/story-to-visual-compiler.md`; these fields establish the project intention at Step 4 and are the evidence for Step 5.

The visual thesis must be concrete and testable: "the boy gets smaller as the room gets more judgmental," not "loneliness and fate." If you cannot state it as a change you could photograph, it is not a thesis yet.

**Non-narrative work skips this block.** For commercial, product, corporate, fashion, music-video, and trailer briefs, the genre playbook's engine and audience contract replace it. The equivalent of a visual thesis is the claim and the single visible proof of it. Do not derive subtext for a lipstick.

### 3. Beat map

Break the scene into beats, not shots. A beat is a change in the balance of pressure — not a change of camera. Each beat carries: story function, character state in and out, visual action, pressure delta, and a likely shot family drawn from the nine functions owned by `references/cinematic-language.md` — establishing, relation, close-up, insert/detail, reaction, transition, aftermath, point-of-view, reveal. Do not coin others; Step 9 has to look the word up.

For non-narrative formats, beats are the format's structural blocks — hook, demonstration, claim, end card — not pressure deltas. Template: `assets/beat-sheet-template.md`.

### 4. Film visual constitution

For a feature, short film, sequence spanning multiple scenes, or any project expected to continue later, load `references/film-consistency-system.md`. Establish the immutable core and the `open → rise → turn → climax → release` visual arc before writing local scene rules. Copy `assets/film-visual-constitution.yaml` for the human-readable contract and `assets/project-state-template.json` for machine state.

Each scene inherits the constitution, applies its arc-position changes, and logs any exception with a reason, scope, and restore point. One-off scenes may keep the same structure in the director's book without creating files.

### 5. Automatic visual direction

For narrative work, load `references/story-to-visual-compiler.md`. Score 3–5 plausible scene archetypes from the director read, choose one primary archetype, and compile it into audience relation, staging, coverage, composition, lens, camera, light/color, sound, and editing decisions. For multi-scene work, constrain every candidate by the Step 4 constitution and current arc position. For a one-off scene, make the automatic decision first and record its lightweight local constitution at Step 7.

Output one plan with a confidence margin and the three decisive pieces of story evidence. Do not ask the user to choose when the margin is at least 10. Do not choose a named director automatically.

### 6. Style reference selection (optional)

If the user names a director or says "in the style of X," load at most one file from `references/director_styles/` as a method reference. Translate its useful ideas into functional rules, then accept only those that serve the Step 5 dramatic decision and fit the Step 4 visual constitution.

Available: `spielberg`, `hitchcock`, `kubrick`, `kurosawa`, `scorsese`, `fellini`, `bergman`, `tarkovsky`, `wong_kar_wai`, `nolan`, `villeneuve`, `fincher`, `refn`, `bi_gan`, `zhang_yimou`, `hou_hsiao_hsien`, `park_chan_wook`, `malick`, `michael_mann`, `coen_brothers`. Index and how to add more: `references/director_styles/README.md`.

Precedence: **dramatic evidence > film visual constitution > visual arc position > scene archetype > user functional preference > genre defaults > skill defaults.** If the user names multiple directors, do not merge their style packages. Translate each request into a function such as information control, exact camera placement, color role, or compositional pressure; use only compatible functions and say what was rejected in one line.

Style modules describe high-level methods. Never copy specific shots, lines, characters, or plots from real films.

### 7. Director's book / visual treatment

The reusable rule set that stabilizes every later prompt: tone and genre, lens and framing policy, camera grammar and the allowed move set, lighting logic and key direction, palette and color script, production design and era lock, performance register, editing rhythm and target ASL, sound direction, and the **invariant clauses** — the identity strings and lighting invariant that get pasted verbatim into every prompt.

Template: `assets/director-book-template.md`. Depth: `references/lighting-and-color.md`, `references/genre-playbooks.md`.

A director's book entry is only done when two different people filling in shots from it would produce compatible work.

### 8. Blocking and staging

For every shot with people: start position, movement, interaction with space and props, camera relationship, end position, eyeline. Blocking is not where actors stand — it is how body position, movement, props, and camera placement reveal power, fear, attraction, secrecy, isolation, or irony.

Depth: `references/blocking-and-staging.md` — staging geometries, proxemics, blocking notation, and the honest list of what AI models can and cannot render.

### 9. Shot list and coverage

Build rows only after beats and blocking are clear. Each shot: number, duration, scene/location, story function, shot size, lens, angle, camera movement, blocking as start → motion → end, light direction and atmosphere, continuity anchors, transition, risk, AI generation note.

Template: `assets/shot-plan-template.md`. Grammar: `references/cinematic-language.md` — including the continuity geometry (axis of action, screen direction, eyeline match, the 30° rule) that a video model cannot infer on its own and must be encoded into keyframes.

### 10. Keyframe strategy

Choose by continuity risk:

- **Single first frame** — simple action inside one stable composition.
- **First + last frame** — controlled transformation, a definite action endpoint, or a designed transition between two compositions.
- **Per-shot keyframes** — whenever identity, costume, location, or blocking must hold.
- **Storyboard panels** — when the model treats stills as slides. Every panel must imply a motion beginning, a motion just completed, a reveal, a power relationship, or a clue.

Template: `assets/keyframe-prompt-template.md`. Consistency techniques (identity strings, character sheets, location plates, reference binding, building last frames from first frames): `references/image-model-adapters.md`.

Gate: do not generate video from a keyframe that has not passed the pre-generation checks in `assets/qc-checklist.md`.

### 11. Tool adapter selection

Identify the control surface before writing a word. Read `references/ai-video-tool-adapters.md` (video) or `references/image-model-adapters.md` (stills). If the tool is unfamiliar, do not guess its features — ask which of these it exposes, or route by capability: text-to-video, image-to-video first frame, last-frame slot, reference binding, camera controls, motion strength, native audio, duration range, extend, multi-shot timestamps, seed, negative prompt.

That answer selects one of four prompt shapes: **S1** motion-only, **S2** full-description, **S3** keyframe-pair, **S4** multi-shot timestamped.

### 12. AI video prompt construction

**S1 — motion-only** (image-to-video). The image already carries identity, composition, lighting, setting, costume, and style. The text defines motion, camera, timing, end state, and constraints — in this order:

```text
[Camera behavior]. [Subject starts in visible state], then [one primary action with pace and direction].
[Environment reacts subtly]. End with [clear final pose/composition].
Maintain [identity / costume / location / light direction]. Avoid [failure modes matching this shot's risk].
```

**S2 — full description** (text-to-video). The text carries everything:

```text
[Format/style]. [Subject with concrete visual identity]. [Location + era + time + atmosphere].
[Primary action]. [Shot size + lens + angle + camera movement]. [Light source + direction + quality].
[Composition]. [Audio if supported]. [Constraints].
```

**S3 — keyframe pair** (first frame + last frame). Describe the bridge between two approved stills, not the stills themselves:

```text
Start from the first image and end on the second. Between them, [one continuous transformation].
Camera [path, or explicitly locked]. Motion [eases in / holds a constant rate / accelerates once].
Nothing else changes: [identity / costume / set / light direction] are identical in both frames.
Avoid [failure modes matching this shot's risk].
```

**S4 — multi-shot timestamped**:

```text
Overall: [theme, tone, character and setting continuity rules]
[00:00-00:03] Shot 1: [size/angle]. [action]. Camera [move]. [emotion]. [sound]
[00:03-00:06] Shot 2: [size/angle]. [action]. Camera [move]. [emotion]. [sound]
```

Templates: `assets/video-prompt-template.md`. Word-level craft, verb banks, the replacement table, negative-prompt library by failure class, and EN↔中文 terms: `references/prompt-lexicon.md`.

### 13. Sound and dialogue plan

Sound is a directing decision, and in AI film it is the cheapest continuity glue available — one continuous ambience bed under a sequence hides an enormous amount of visual drift.

Plan five layers per shot: room tone, ambience, foley, spot effects, score. For dialogue, respect the one-speaker-per-clip rule, label speakers unambiguously, and convert what cannot be generated into voice-over, off-screen line, or reaction-only. Template: `assets/sound-plan-template.md`. Depth: `references/sound-and-dialogue.md`.

### 14. Edit and assembly plan

Generated clips are raw material, not a cut. Specify the timeline: clip order, in/out points, trim handles, transition into each clip, and the audio layers running underneath. Design match cuts by building shot A's last frame and shot B's first frame together. Fix what can be fixed with a cutaway, a trim, or a dissolve rather than another generation.

Template: `assets/edit-timeline-template.md`. Depth: `references/editing-and-assembly.md`.

### 15. QC, repair, and state write-back

Gate with `assets/qc-checklist.md` when scoring a clip or a batch. When something fails, diagnose in this order — each bucket with the codes it usually resolves to:

1. **Asset mismatch** — references too inconsistent to hold identity (F1, F3, F16).
2. **Prompt overload** — over motion budget: too many actions or camera moves (F4, F6).
3. **Weak action** — the frame is a pose, not an implied motion (F2).
4. **Missing endpoint** — the prompt never says where the action ends (F5).
5. **Bad camera logic** — the move contradicts the framing or the space (F6).
6. **Continuity gap** — shot A's end state does not match shot B's start state (F7).
7. **Tool mismatch** — asking a model for a feature it does not control (F6, F11).

If the user reports more than one symptom, collect **every** code before proposing anything. Multiple codes usually share one root decision, and fixing them one at a time re-spends the same generation.

Then apply the **cost ladder** in order — prompt edit, parameter change, regenerate, rebuild the keyframe, re-plan the shot, fix in the edit, cut the shot — and stop at the first level that works. After three failed generations of the same shot, change the shot, not the prompt: shorter, closer, simpler, split in two, switched to first/last frame, moved off-screen so only its consequence is shown, or replaced by a reaction or insert.

Never run a diagnostic questionnaire. Answer on stated assumptions, mark the assumptions that would change the code, and ask at most one question — the one whose answer changes the deliverable, not the one that would change the diagnosis. Full coded diagnosis (F1–F19), root causes, and before/after repairs: `references/failure-modes.md`.

For multi-scene work, close the loop after every reviewed clip. Mark it `accepted`, `rejected`, or `repair`; only an accepted clip writes `observed_start` and `observed_end` into `project-state.json`. The next planned shot inherits the latest accepted `observed_end`, not the earlier `planned_end`. Run `python3 scripts/validate_project_state.py <project-state.json>` before generating the next scene. Full merge and drift rules: `references/film-consistency-system.md`.

Repair output format:

```markdown
## Diagnosis
- [failure code and the specific mechanism, not a generic guess; one line per code if several]
- [shared root: the one decision that produced them all]

## Fix
- [the cheapest change on the cost ladder that addresses the root]

## Revised prompt
[clean prompt]

## Why this should work
[one or two sentences tied to the mechanism]
```

If no prompt, keyframe, or clip was supplied, do not invent the user's shot to fill the third section. Output the diagnosis, the fix ladder, and the prompt *shape* to rewrite into, then ask for the original prompt and which input mode produced it.

## Output modes

| Mode | Name | Use when | Template |
|---|---|---|---|
| A | Director Analysis | Interpretation, subtext, direction rules | inline (below) |
| B | Beat Sheet | Structure before shots | `assets/beat-sheet-template.md` |
| C | Director's Book / Film Constitution | Rules that govern one scene or the whole piece | `assets/director-book-template.md`, `assets/film-visual-constitution.yaml` |
| D | Shot Plan | Shot list, storyboard plan, shooting table | `assets/shot-plan-template.md` |
| E | Keyframe Prompt Pack | Stills, first/last frames, panels, character sheets | `assets/keyframe-prompt-template.md` |
| F | Video Motion Prompt Pack | Assets exist, motion prompts needed | `assets/video-prompt-template.md` |
| G | Continuity / Project State | Recurring characters, locations, accepted-footage state | `references/continuity-bible.md`, `assets/project-state-template.json` |
| H | Sound & Dialogue Plan | Sound design, music, dialogue, VO | `assets/sound-plan-template.md` |
| I | Edit & Assembly Plan | Cutting generated clips into a sequence | `assets/edit-timeline-template.md` |
| J | QC & Repair | Something failed or looks wrong | `assets/qc-checklist.md` |
| K | Production Control | Staged execution, model calls, versions, gates, recovery, delivery | `assets/production-plan-template.json`, `references/production-control-plane.md` |

Mode A shape:

```markdown
# Director Analysis

## Dramatic core
[one paragraph]

## Subtext
[one paragraph]

## Emotional arc
[opening → escalation → turn → closing]

## Visual thesis
[one concrete, photographable rule]

## Direction rules
- Camera:
- Lens:
- Lighting:
- Palette:
- Performance:
- Editing:
- Sound:
```

## Gotchas

- More cinematic adjectives is not better direction. Cut every word that does not change a decision.
- Beautiful shots that do not advance the story are the most expensive thing in the plan.
- Do not write "camera rotates, zooms, pans, tracks, and shakes" unless the tool controls that combination and the story needs it.
- Given reference images, do not reinvent costume, face, era, or location — describe them and lock them.
- "Looks like a slideshow" is not fixed by adding "cinematic motion." Add a specific subject action, an environmental reaction, and a motivated camera behavior with an end state.
- Horror and suspense want controlled stillness, negative space, delayed revelation, and sound — not constant camera movement.
- Comedy wants a wider frame, a longer hold, and a flat register. Do not direct a joke like a tragedy.
- Vertical is not cropped horizontal. Stage in depth, scale the subject up, and rethink every wide shot.
- Literary or satirical material keeps its idea. Do not convert every scene into genre spectacle.
- If the user reports a failure, diagnose the mechanism before rewriting. A rewrite that does not name the cause is a guess.

## Minimal working example

Request: "I have a keyframe of a man kneeling in a rainy old street. Make it into an AI video prompt."

Good:

```text
Low-angle medium close shot, locked camera with a very slow push-in, no other movement. The man starts
on one knee in the muddy rain-soaked street, one hand pressed flat against the wet ground. He breathes
heavily, then slowly lifts his head just far enough for his wet hair to fall aside and reveal one tired
eye. Rain keeps breaking the surface of the puddles around his robe; distant lightning briefly lifts the
far end of the empty street. End with him half-raised and still unsteady, weight on the forward knee,
head up. Maintain the same face, wet hair, crimson robe, stormy old Chinese street, low-key practical
lighting from a single lantern camera-left. No text, no watermark, no extra people, no plastic, no
rubber soles, no wristwatch, no printed logos.
```

Bad:

```text
Cinematic dark horror atmosphere, the man struggles in the rain and remembers his past, dramatic camera,
emotional, high quality, masterpiece.
```

The good version names a camera behavior and forbids the rest, gives one primary action with visible body mechanics, adds one environmental reaction, states an end state, repeats the invariants, and lists only the negatives this shot actually risks — as nameable instances, not as the category "modern objects," which a model cannot resolve. The bad version names a mood and hopes.

## Self-check before responding

- Does every shot have a story function you could state in one clause?
- Does the automatic direction name one primary archetype, a confidence margin, and three decisive pieces of story evidence?
- For multi-scene work, does every local choice inherit the film constitution, follow the visual arc, or log a bounded exception?
- Is there exactly one dominant camera move per clip — or a deliberate none?
- Does every action have a start state and an end state?
- Are the invariants written verbatim, identically, in every prompt of the scene?
- Do adjacent shots differ by at least two size steps, unless matched on purpose?
- Are the negatives limited to this shot's real risks?
- If a director was named, did the answer translate that reference into functional methods instead of copying or mixing a complete style package?
- Has every accepted clip written its observed end state before the next shot is planned?
- Is the output proportional to what was asked?

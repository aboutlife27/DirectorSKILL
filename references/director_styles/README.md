# Director Style Modules

Style references used by the main `cinematic-director` skill. Each file records transferable narrative and visual methods. The main workflow may borrow only the functions that fit the scene evidence and the film constitution; a named director never becomes a global override.

These are **reference modules**, not standalone skills. The host agent never loads them on its own. The main skill may pull one in at Step 6（可选风格功能参考）when a style is named.

> Usage rule: these files describe high-level methods only. Do not copy specific shots, lines, characters, plots, or other copyrighted expression from any director's actual films. Use them to inform original work.

## Available styles

| File | Director | Lens in one line |
|---|---|---|
| [01_spielberg.md](01_spielberg.md) | Steven Spielberg 斯皮尔伯格 | Emotional mainstream narrative, family stakes, awe via reaction-before-reveal |
| [02_hitchcock.md](02_hitchcock.md) | Alfred Hitchcock 希区柯克 | Suspense via shared knowledge, voyeurism, mistaken identity, controlled withholding |
| [03_kubrick.md](03_kubrick.md) | Stanley Kubrick 库布里克 | Wide-angle one-point perspective, central symmetry, cold formalism, allegory |
| [04_kurosawa.md](04_kurosawa.md) | Akira Kurosawa 黑泽明 | Epic ensemble, humanism, weather as drama, dynamic action space |
| [05_scorsese.md](05_scorsese.md) | Martin Scorsese 斯科塞斯 | Urban energy, guilt, voice-over, kinetic camera, moral fall |
| [06_fellini.md](06_fellini.md) | Federico Fellini 费里尼 | Dream logic, circus parade, autobiographical fantasy, grotesque tenderness |
| [07_bergman.md](07_bergman.md) | Ingmar Bergman 伯格曼 | Interior psychological drama, face close-ups, existential silence |
| [08_tarkovsky.md](08_tarkovsky.md) | Andrei Tarkovsky 塔可夫斯基 | Poetic time, long takes, water and fire, spiritual search |
| [09_wong_kar_wai.md](09_wong_kar_wai.md) | Wong Kar-wai 王家卫 | Urban loneliness, missed connections, neon, fragmented memory, voice-over |
| [10_nolan.md](10_nolan.md) | Christopher Nolan 诺兰 | High-concept structure, time puzzles, large-format practical spectacle |
| [11_villeneuve.md](11_villeneuve.md) | Denis Villeneuve 维伦纽瓦 | Monumental scale, mist, geometric symmetry, low-rumble silence |
| [12_fincher.md](12_fincher.md) | David Fincher 芬奇 | Surgical control, teal-amber palette, procedural investigation, restrained dread |
| [13_refn.md](13_refn.md) | Nicolas Winding Refn 雷弗恩 | Neon color blocks, symmetric ritual framing, slow gaze, sudden violence |
| [14_bi_gan.md](14_bi_gan.md) | Bi Gan 毕赣 | Misty SW-China small towns, single ultra-long take, dream-memory loops, poetic voice-over |
| [15_zhang_yimou.md](15_zhang_yimou.md) | Zhang Yimou 张艺谋 | Saturated single-color fields, mass choreography as image, folk-ritual iconography |
| [16_hou_hsiao_hsien.md](16_hou_hsiao_hsien.md) | Hou Hsiao-hsien 侯孝贤 | Static distant long take, doorway depth, real-time duration, ellipsis over exposition |
| [17_park_chan_wook.md](17_park_chan_wook.md) | Park Chan-wook 朴赞郁 | Baroque revenge, improbable camera geometry, ironic beauty over cruelty, pattern density |
| [18_malick.md](18_malick.md) | Terrence Malick 泰伦斯·马力克 | Available light, wide wandering handheld at magic hour, whispered voice-over, associative montage |
| [19_michael_mann.md](19_michael_mann.md) | Michael Mann 迈克尔·曼 | Urban night as a cold luminous field, professional competence, loneliness as architecture |
| [20_coen_brothers.md](20_coen_brothers.md) | Coen Brothers 科恩兄弟 | Deadpan geometric dark comedy, wide low-mounted moves, fate as a flat joke |

## Module structure

Every module follows the same section order, so switching lenses is a diff and adding one is a template fill:

```markdown
## 一句话镜头 / Lens in one line              # bilingual, 20 words or fewer each
## 适用场景                                   # when to reach for it
## 反例：这不是什么 / What this lens is NOT     # the adjacent styles it gets confused with
## 核心风格关键词
## 叙事方法
## 镜头语言
## 灯光与色彩
## 剪辑节奏
## 声音与音乐
## 人物与表演
## 风格参数 / Style parameters                # machine-readable YAML override block
## 可迁移拍摄清单                              # 6-8 checkable imperatives
## 提示词模板 / Prompt templates               # 中文 brief（Steps 7/9）+ EN keyframe（Step 10）+ EN i2v（Step 12）
## 落地示例 / Worked example                   # the shared control scene, this lens
```

Two sections carry most of the practical weight:

- **反例 / What this lens is NOT** names the adjacent style each director gets confused with and states the mechanical difference. Kubrick and Fincher are both cold and controlled — one presses people into architecture with a wide lens and one-point perspective, the other isolates them as specimens with long-lens compression. That distinction is what makes an overlay produce a different film rather than a different adjective.
- **风格参数 / Style parameters** is a YAML block the main skill reads directly: lens kit in mm, preferred and forbidden camera moves, motion budget, shot-size bias, composition patterns, key quality and ratio, palette, average shot length, transition bias, sound policy, performance register, aspect bias, AI clip duration and prompt bias, and the negative-prompt additions that style requires.

Every module ends by directing the **same** control scene — a person stands at a closed apartment door holding something they intend to give away, hesitates, and leaves without knocking — so the twenty files can be read side by side as twenty answers to one question.

## See it in action

[example_comparisons.md](example_comparisons.md) takes a single scene and treats it under eight lenses back to back, with a matrix of what each one changes and a "choosing a lens" table covering all twenty. Load it at Step 6 only when the user requests a named reference or a functional comparison. The comparison is advisory; the story evidence and film constitution always win.

## How the main skill uses these

1. The user names a director or asks for "in the style of X" — or supplies one via `project.director_style` in the intake schema.
2. The main skill loads at most one file and translates only compatible methods into functional constraints for:
   - Step 7（导演本）：镜头政策、相机语法、灯光、色彩、剪辑、声音与表演
   - Step 9（镜头表）：镜头族、运动、平均镜长与画幅改写
   - Step 10（关键帧）：色彩角色、光比、构图和负向约束
   - Step 12（AI 视频提示）：运动描述、片段长度和负向约束
3. 优先级固定为 **戏剧证据 > 视觉宪法 > 全片弧线 > 场景原型 > 用户指定的功能性参考 > 类型惯例 > 默认值**。
4. If no director is named, the main skill uses [../genre-playbooks.md](../genre-playbooks.md) and the project's own tone.

同一场最多读取一个风格文件，但只提取能服务戏剧功能的做法。若用户点名多个导演，先说明每个名字对应的功能，再选择与当前场景证据最一致的一组方法；不得拼贴签名画面，也不得突破视觉宪法。

## Adding a new style

Create `NN_<slug>.md` in this directory using the section structure above, with a complete `风格参数` YAML block and the shared worked-example scene. Then register it in `SKILL.md`（Step 6）, the table above, and the top-level `README.md` tables. Add an eval case that proves the functional reference changes camera, palette, pacing, or sound without overriding story evidence or the film constitution, plus a copyright-safety assertion. Full instructions: [../../CONTRIBUTING.md](../../CONTRIBUTING.md).

The numbered prefix controls sort order; pick the next available number.

Three conventions the twenty existing modules already follow, so a new one does not have to be special-cased:

- **`S` is reserved.** `S1`–`S4` mean the four prompt shapes in [../ai-video-tool-adapters.md](../ai-video-tool-adapters.md) and nothing else. Number the shots in a worked example `SH1`, `SH2`, … and any generation chain `CH1`, `CH2`, …
- **Keep the three prompt-template labels verbatim** — `镜头设计简报（中文，用于 Step 7 导演本与 Step 9 分镜）：`, `Image-to-video motion prompt（英文，用于 Step 12，接关键帧）：`, `Keyframe / still prompt（英文，用于 Step 10，接图像模型）：` — so a reader can tell at a glance which pipeline step each block feeds.
- **Restate the composition rule for 9:16 where the signature depends on a wide frame.** If the lens leans on lateral ranks, extreme lateral symmetry, or a figure sized as a percentage of frame height at distance, add one bullet at the end of `镜头语言` beginning `竖幅（9:16）改写：` saying what the rule becomes vertically — never that it is abandoned — and point the `aspect_bias` line at it with a comment. Modules 04, 06, 11, 15, 16 and 19 carry one; the rest do not need it. The general vertical grammar is owned by [../cinematic-language.md](../cinematic-language.md).

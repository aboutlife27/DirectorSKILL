# 导演方法与技术来源

本文件用于解释设计依据或扩展方法库，不是日常分镜必须加载的风格菜单。只提取可迁移的功能原则，不复刻具体影片镜头、角色、对白或在世创作者的完整个人风格。

## 经典方法

| 来源 | 可迁移原则 | 在本 skill 中的用途 |
|---|---|---|
| Alfred Hitchcock / AFI 大师课 |  exposition 应借可观看的行动传递；悬念先管理观众知道什么 | `audience_information`、揭示时机、行动化说明 |
| Sidney Lumet / *12 Angry Men* | 焦段、景深、机位和调度可以沿整片逐步改变空间压力 | 全片视觉弧，而非逐场随机换风格 |
| Vittorio Storaro / ASC 访谈与案例 | 光与颜色承担人物、观念和意识状态 | `palette_roles` 与颜色出现、污染、消失、回归 |
| Akira Kurosawa / Criterion 研究 | 人物、摄影机、天气和环境形成多层运动；极端天气可外化冲突 | 动作可读性、画内运动分层、环境叙事 |
| Orson Welles / Gregg Toland / ASC 案例 | 深焦、层次调度与构图可在一个镜头中保持空间关系 | 权力依赖同框空间时优先调度而非切碎 |
| Walter Murch / Rule of Six | 剪辑冲突时优先情绪、故事、节奏，再考虑视线与空间连续性 | 分镜选择、切点与修复优先级 |

来源：

- [AFI：Hitchcock 大师课节选](https://americanfilm.afi.com/issue/2012/7/conservatory)
- [Criterion：Lumet 如何用焦段改变空间与感受](https://www.criterion.com/current/posts/2076-12-angry-men-lumet-s-faces)
- [DGA：Sidney Lumet 视觉口述史](https://www.dga.org/craft/visualhistory/interviews/sidney-lumet)
- [ASC：Storaro 谈红、绿、蓝的表达体系](https://theasc.com/articles/whos-afraid-of-red-green-and-blue)
- [ASC：Storaro 在《巴黎最后的探戈》中的暖冷设计](https://theasc.com/articles/flashback-last-tango-in-paris)
- [Criterion：黑泽明的运动、剪辑与天气](https://www.criterion.com/current/posts/1539-eclipse-series-23-the-first-films-of-akira-kurosawa)
- [ASC：《公民凯恩》的深焦与现实感](https://theasc.com/articles/realism-for-citizen-kane)
- [Filmmaker Magazine：Walter Murch 的六项剪辑标准](https://filmmakermagazine.com/100389-watch-walter-murchs-six-criteria-while-editing/)

## 现代长视频系统

| 成果 | 可迁移原则 | 采用方式 |
|---|---|---|
| Seedance Sequence | 先做导演读法；项目正史与瞬态分离；已验收镜头回写 | 结构化剧情证据、单一项目状态、计划/实际状态 |
| VideoMemory | 角色、道具、背景的动态记忆库按镜头检索与更新 | `canonical` 与验收后状态更新 |
| StoryBlender | 全局资产与单镜变量解耦，以连续性记忆图连接分镜 | 不可变核心、局部状态和镜头谱系 |
| Camera Artist | 专职摄影决策与递归分镜以维持镜头间叙事连续 | 场景原型到覆盖语法的编译 |
| DrawVideo | 全局多镜头规划与局部单镜运动分层 | 视觉宪法/视觉弧与逐镜动作分离 |
| DreamShot | 多参考角色条件与跨镜头身份约束 | 角色参考资产与身份一致性 |

来源：

- [Seedance 2.0 skill 仓库](https://github.com/Emily2040/seedance-2.0)
- [VideoMemory 论文](https://arxiv.org/abs/2601.03655)
- [StoryBlender 论文](https://arxiv.org/abs/2604.03315)
- [Camera Artist 论文](https://arxiv.org/abs/2604.09195)
- [DrawVideo 论文](https://arxiv.org/abs/2605.23508)
- [DreamShot 论文](https://arxiv.org/abs/2604.17195)

## 采用边界

- 市场 skill 只作为方法研究，不成为项目状态所有者。
- 不引入 Atlas Cloud、Hermes Kanban 或 `genmedia` 等供应商运行时依赖。
- 论文中的专有训练组件不在本 skill 中假装实现；这里只采用其状态分层和工作流思想。
- 大师方法只在剧情证据支持时启用，不以姓名覆盖视觉宪法。

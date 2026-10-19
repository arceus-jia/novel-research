# novel-research · 小说研究助手

novel-research 是面向本地 Agent 的小说阅读与研究技能，适用于 Codex、Claude Code、DeepSeek Harness、OpenCode 和 WorkBuddy 等能够读取文件、执行工具的宿主。

它使用宿主当前配置的模型，帮助你按原文研究人物、剧情和伏笔，分批粗读或精读，并保存研究成果、讨论要点和纠错，供后续会话继续使用。

首次使用按下面五步操作。已经安装并准备好资料的用户，可以直接查看[开始阅读](#4-接入资料并开始阅读)或[提问与继续研究](#5-提问与继续研究)。

## 1. 准备环境

需要以下条件：

- 已能正常使用的本地 Agent，能够读取小说文件、执行 Python，并向书籍目录写入研究结果。
- [Python 3.11 或更高版本](https://www.python.org/downloads/)。
- 一部 EPUB、TXT 或 Markdown 格式的小说。

本技能仅依赖 Python 标准库，无需安装额外 Python 包、数据库服务或配置额外模型 API Key。模型的登录、配置和用量由宿主管理。

安装 Python 后，在终端检查版本：

```sh
# macOS
python3 --version
```

```powershell
# Windows PowerShell
py -3 --version
```

Windows 未提供 `py` 命令时，可使用 `python --version` 检查。环境安装或执行遇到问题时，参阅[安装与排障](skills/novel-research/references/setup.md)。

## 2. 安装技能

[下载本仓库源码 ZIP](https://github.com/arceus-jia/novel-research/archive/refs/heads/main.zip) 并解压，找到其中的 [skills/novel-research](skills/novel-research) 文件夹。

将整个 `novel-research` 文件夹复制到所用宿主的技能目录，保留 `SKILL.md`、`scripts/`、`references/` 和 `assets/`。表中路径是复制后的最终文件夹，其中直接包含 `SKILL.md`。

用户级安装可用于多个项目；项目级安装随对应项目使用。选择一种安装方式即可：

| 宿主 | 用户级安装位置 | 项目级安装位置 |
| --- | --- | --- |
| Codex | `~/.agents/skills/novel-research` | `<项目>/.agents/skills/novel-research` |
| Claude Code | `~/.claude/skills/novel-research` | `<项目>/.claude/skills/novel-research` |
| DeepSeek Harness | `~/.dsh/skills/novel-research` | `<项目>/.dsh/skills/novel-research` |
| OpenCode | `~/.config/opencode/skills/novel-research` | `<项目>/.opencode/skills/novel-research` |
| WorkBuddy（代码开发模式） | `~/.codebuddy/skills/novel-research` | `<项目>/.codebuddy/skills/novel-research` |

`~` 表示用户主目录：macOS 通常为 `/Users/用户名`，Windows 通常为 `C:\Users\用户名`。例如，Windows 的 Codex 用户级安装位置为 `%USERPROFILE%\.agents\skills\novel-research`。

安装后，刷新或新建 Agent 会话，确认技能已被识别。项目级安装应在宿主中打开对应项目。

DeepSeek Harness 需要启用技能加载功能；它和 OpenCode 也支持 `.agents/skills` 兼容目录。WorkBuddy 还支持通过技能界面上传完整技能 ZIP。这些安装方式及宿主配置详见[安装与排障](skills/novel-research/references/setup.md)。

## 3. 放入小说资料

为每部小说建立一个独立的书籍目录，将正文放进去。**只有一份正文文件也可以开始**，访谈、评论和笔记可以以后再补充。

书籍目录应位于技能安装文件夹之外。下面以 Codex 项目级安装为例，其他宿主的技能目录按上表替换：

```text
研究项目/
├── .agents/
│   └── skills/
│       └── novel-research/       # 安装好的技能
└── 书名/                        # 这部小说的资料与研究结果
    ├── 正文.epub
    └── 参考资料/                # 可选
        ├── 访谈.md
        └── 读者评论.txt
```

书籍名称、文件名和子目录均可自行组织。正文可以是一整本文件，也可以按卷册或章节拆分；资料可放在任意子目录。Agent 会检查正文顺序和资料性质，接入文件并维护来源配置。

普通、未加密的文字 EPUB 会由内置工具自动转换，原包保留。其他格式与提取范围见[资料格式说明](#资料格式说明)。网页参考链接也可在对话中提供，由 Agent 使用宿主已有浏览能力获取和归档。

## 4. 接入资料并开始阅读

推荐首次使用先执行 `init` 接入资料，再执行 `analyze` 精读建库。需要先快速了解全书时，第二步使用 `skim` 粗读。

**以下命令发送到 Agent 对话窗口。** 将 `./书名` 替换为实际书籍目录，先发送接入命令，完成后再发送阅读命令：

| 宿主 | 第一步：接入资料 | 第二步：精读全书 |
| --- | --- | --- |
| Codex CLI / IDE | `$novel-research init ./书名` | `$novel-research analyze ./书名` |
| Claude Code | `/novel-research init ./书名` | `/novel-research analyze ./书名` |
| OpenCode（支持技能斜杠入口的版本） | `/novel-research init ./书名` | `/novel-research analyze ./书名` |
| 其他兼容宿主 | 使用 novel-research init ./书名 | 使用 novel-research analyze ./书名 |

粗读时，将第二条命令中的 `analyze` 换为 `skim`，例如：

```text
$novel-research skim ./书名
```

两种阅读方式的区别：

| 动作 | 适合的目标 | 保存的内容 |
| --- | --- | --- |
| `skim` | 先了解全书结构，方便定位后续问题 | 简短情节梗概、主要人物、关键事件与待查线索 |
| `analyze` | 深入研究人物、认知变化、伏笔和叙述方式 | 详细章节记录、具体判断与引文，再围绕专题跨章复核 |

**`skim <目录>` 和 `analyze <目录>` 默认处理全书。** 可在命令后明确限定章节，或描述关注点；关注点本身不缩减阅读范围。例如：

> 使用 novel-research analyze ./书名，只精读第一至二十章，重点记录人物认知变化和伏笔候选。

Agent 会逐批阅读并保存结果。粗读和精读分别记录完成度，可先粗读再精读；精读会参考已有概览，并完成独立的精读任务。两种方式都使用宿主当前模型的用量，长篇可能需要多次会话。

`./书名` 表示当前项目下的书籍目录，也可以使用完整路径。同一会话中目录已经明确时可省略。需要查看帮助时，在宿主的技能入口后使用 `help`，例如 `$novel-research help` 或 `/novel-research help`。

## 5. 提问与继续研究

日常研究直接用自然语言提问，提供书籍目录和问题即可。也可以在全书建库完成前开始讨论，Agent 会按需接入资料、查询已有记录并回查原文。

> 小说资料在「./书名」。请依据前十章，分析主角从怀疑到确认身份的过程，并给出原文出处。

> 这条人物动机的解释与前文是否矛盾？请给出相关原句和前后文，并比较其他可能的解释。

关键判断会回查原文，区分正文叙述、人物说法、人物认知与研究推断。遇到分歧时，可要求对照原文讨论；重要发现、假设和纠错可保存，供以后继续使用。

换会话后，提供同一书籍目录并使用 `resume`，例如在 Codex 中发送：

```text
$novel-research resume ./书名
```

Claude Code 或支持斜杠入口的 OpenCode 使用 `/novel-research resume ./书名`；其他兼容宿主可发送“使用 novel-research resume ./书名”。Agent 会检查资料变化、读取有效纠错与进度，按保存的阅读模式和章节范围继续。显式使用 `skim` 或 `analyze` 则按新的阅读要求执行。

研究要点和进度保存在书籍工作区；完整聊天不会自动逐句存档。模型的上下文处理和会话持续执行由宿主管理，技能提供可恢复的任务与记录。

## 添加资料与保存成果

| 需要做的事 | 操作方式 |
| --- | --- |
| 新增文件或子目录 | 放入同一书籍目录，然后继续研究，或提出“接入新增资料”。Agent 会递归检查并增量接收，沿用已有记录。 |
| 更新已有正文 | 正文变更后，要求 Agent 更新资料。工具会准备新来源版本，保留历史引用，并标记受影响的旧分析。 |
| 添加网页链接 | 发送链接及用途，例如“将这篇访谈作为补充资料保存并注明出处”。无法获取或仅获取部分内容时会说明范围。 |
| 记住发现或更正 | 直接提出保存要求。Agent 会核对相关原文，区分已查证判断与待核查意见，并保存要点。 |
| 研究另一部小说 | 为新书建立独立目录，并提供新的路径。 |

在开始、恢复研究或收到更新请求时，Agent 才会执行资料检查和接入。

结果路径均相对于书籍目录：

| 内容 | 位置 |
| --- | --- |
| 资料库入口与粗读、精读进度 | `analysis/views/library.md` |
| 章节、人物称谓和疑问导航 | `analysis/views/` |
| 每批阅读的详细记录 | `analysis/records/` |
| 专题研究报告 | `research/<主题>/report.md` |
| 讨论要点、纠错与阶段进度 | `notes/` |
| 由 Agent 维护的来源配置 | `novel.json` |

导航和报告在完成相应分析或研究后生成。需要报告时，可在请求中说明输出目标。

备份或迁移时，复制整个书籍目录，包括原始资料、研究记录和隐藏目录 `.novel/`。升级技能时替换技能安装文件夹，保留书籍目录；升级后可要求 Agent 检查环境与资料。

## 资料格式说明

| 资料形式 | 处理方式与范围 |
| --- | --- |
| EPUB | 提取普通、未加密文字书的正文，保留原包与来源映射。图片、固定版式和受保护正文不在自动转换范围内；遗漏内容会提示核对。引用定位到转换文本和包内资源文件，不提供排版页码。 |
| TXT / Markdown | 按原位置登记和读取，支持整本、多文件和嵌套目录。 |
| 网页链接 | 使用宿主已有浏览能力获取实际内容，保存来源信息；未取得的内容保留待获取状态。 |
| PDF 等其他格式 | 先提供可读的 TXT 或 Markdown 版本，再接入研究。 |

正文用于核对小说事实；作者说明、访谈、读者评论和个人笔记保留各自的来源性质。正文顺序、资料版本或身份存在影响研究的歧义时，Agent 会请求确认。

## 示例与进阶参考

| 资源 | 用途 |
| --- | --- |
| [示例书籍《渡灯记》](skills/novel-research/assets/example) | 六章原创正文与一份读者评论，可复制到独立书籍目录，体验资料接入、查证与建库。 |
| [安装与排障](skills/novel-research/references/setup.md) | 环境检查、宿主安装方式、编码与执行问题。 |
| [资料接收与更新](skills/novel-research/references/intake.md) | 文件、EPUB 和网页资料的接入细节。 |
| [分批粗读](skills/novel-research/references/skim.md) | 简洁线索记录、粗读进度和恢复流程。 |
| [全书精读与资料库](skills/novel-research/references/library.md) | 详细建库、跨章复核和精读恢复流程。 |
| [研究与讨论记录](skills/novel-research/references/research.md) | 专题报告、讨论记忆和纠错的保存方式。 |

宿主官方说明：[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)、[OpenCode](https://opencode.ai/docs/skills/)、[WorkBuddy 技能导入](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market) 与[项目配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)。

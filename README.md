# novel-research · 小说研究助手

novel-research 是面向本地 Agent 的小说阅读与研究技能，适用于 Codex、Claude Code、DeepSeek Harness、OpenCode 和 WorkBuddy 等能够读取文件、执行工具的宿主。

它使用宿主当前配置的模型，将小说正文、补充资料和研究记录组织在独立工作区中，支持原文查证、人物与情节研究、分批精读，以及跨会话的进度和纠错保存。

主要能力：

- **按原文研究**：检索原句、阅读上下文，区分叙述事实、人物说法、人物认知与研究推断。
- **分批精读建库**：逐批保存章节记录、人物称谓和待核查问题，再围绕专题进行跨章复核。
- **持续积累研究**：保存报告、发现、假设、纠错和阶段进度，供后续会话继续使用。

## 运行要求

- 已配置完成的本地 Agent，具备文件读取、Python 执行和工作区写入权限。
- [Python 3.11 或更高版本](https://www.python.org/downloads/)。
- 本地 EPUB、TXT 或 Markdown 小说正文；可同时提供访谈、评论、笔记和参考链接。

本技能仅依赖 Python 标准库，无需额外 Python 包、数据库服务或模型 API Key。模型的登录、配置和用量由宿主管理。

安装 Python 后，可在终端检查版本：

```sh
# macOS
python3 --version
```

```powershell
# Windows PowerShell
py -3 --version
```

Windows 未提供 `py` 命令时，可使用 `python --version` 检查，或向 Agent 指定已安装解释器的完整路径。安装说明覆盖 macOS 和 Windows；当前已在 macOS 本地验证，Windows 实机兼容性尚待验证。

## 安装

将仓库中的 [skills/novel-research](skills/novel-research) 完整文件夹复制到宿主的技能目录，保留其中的 `SKILL.md`、`scripts/`、`references/` 和 `assets/`。

用户级安装适用于多个项目；项目级安装仅在对应项目范围内使用。

| 宿主 | 用户级安装位置 | 项目级安装位置 |
| --- | --- | --- |
| Codex | `~/.agents/skills/novel-research` | `<项目>/.agents/skills/novel-research` |
| Claude Code | `~/.claude/skills/novel-research` | `<项目>/.claude/skills/novel-research` |
| DeepSeek Harness | `~/.agents/skills/novel-research` 或 `~/.dsh/skills/novel-research` | `<项目>/.agents/skills/novel-research` 或 `<项目>/.dsh/skills/novel-research` |
| OpenCode | `~/.agents/skills/novel-research` 或 `~/.config/opencode/skills/novel-research` | `<项目>/.agents/skills/novel-research` 或 `<项目>/.opencode/skills/novel-research` |
| WorkBuddy（代码开发模式） | `~/.codebuddy/skills/novel-research` | `<项目>/.codebuddy/skills/novel-research` |

`~` 表示用户主目录：macOS 通常为 `/Users/用户名`，Windows 通常为 `C:\Users\用户名`。例如，Windows 的 Codex 用户级安装位置为 `%USERPROFILE%\.agents\skills\novel-research`。

安装后，刷新或新建 Agent 会话，确认技能已被识别。项目级安装应在宿主中打开对应项目。DeepSeek Harness 需要启用技能加载功能；使用自定义技能目录时，按宿主配置安装。

WorkBuddy 也支持通过“专家·技能·连接器 → 技能 → 添加技能 → 上传技能”导入完整技能 ZIP。各宿主的加载规则见 [Codex](https://learn.chatgpt.com/docs/customization/overview#skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)、[OpenCode](https://docs.opencode.ai/docs/skills/)、[WorkBuddy 技能导入](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market) 与 [WorkBuddy 项目配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)。

## 准备资料

为每部小说建立独立的书籍目录，放入正文与相关资料。书籍目录应位于技能安装文件夹之外，可以放在项目中，也可以使用其他可访问路径。

以下为采用 `.agents` 项目级安装时的一种组织方式。书籍名称、资料文件名和子目录均可自行组织：

```text
研究项目/
├── .agents/
│   └── skills/
│       └── novel-research/       # 技能安装目录
└── 书名/                        # 单本书籍工作区
    ├── 正文.epub
    └── 参考资料/
        ├── 访谈.md
        └── 读者评论.txt
```

正文可以是一整本文件，也可以按章节或卷册拆分。访谈、评论和笔记可放在同一层或任意子目录。Agent 会读取必要内容，确认正文顺序和资料性质，并生成、维护 `novel.json` 等工作文件。

正文用于核对小说事实；作者说明、访谈、读者评论和个人笔记保留各自的来源性质。顺序、版本或身份存在歧义时，Agent 会请求确认。

| 资料形式 | 接入方式 |
| --- | --- |
| EPUB | 自动调用内置工具提取文字，保留原包与来源映射。适用于普通、未加密的文字书。 |
| TXT / Markdown | 按原位置登记和读取，支持多个文件及嵌套目录。 |
| 网页链接 | 使用宿主已有浏览能力获取内容，保存来源信息后纳入检索。 |

EPUB 转换不包含图片 OCR、固定版式恢复或受保护正文的解密；未提取的附属内容会提示核对。引用定位到转换文本及原包内的资源文件，不提供原书排版页码。PDF 等其他格式需先提供可读的 TXT 或 Markdown 版本。

## 使用

安装并启用技能后，提供书籍目录和研究问题，直接进行自然语言对话：

> 小说资料在「./书名」。请依据前十章，分析主角从怀疑到确认身份的过程，并给出原文出处。

Agent 会按需接入资料、检索原文并开展研究。支持隐式调用的宿主可根据问题自动选用本技能，也可通过宿主技能列表或显式入口选择。Codex 的发现与触发规则见 [官方技能说明](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills)。

### 可选流程命令

需要查看帮助、显式接入资料、安排批量精读或恢复进度时，可在技能入口后提供动作和参数：

| 宿主 | 帮助入口 |
| --- | --- |
| Codex CLI / IDE | `$novel-research help` |
| Claude Code | `/novel-research help` |
| OpenCode（支持技能斜杠入口的版本） | `/novel-research help` |
| 其他兼容宿主 | 加载技能后发送“使用 novel-research help”。 |

本技能已声明 OpenCode 的斜杠入口元数据。各宿主的调用规则见 [Codex](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills)、[Claude Code](https://code.claude.com/docs/en/skills#pass-arguments-to-skills) 和 [OpenCode](https://opencode.ai/docs/skills/#write-frontmatter)。

支持以下动作：

| 动作 | 示例参数 | 用途 |
| --- | --- | --- |
| `help` | `help` | 查看命令和示例，无需指定书籍目录。 |
| `init` | `init ./书名` | 显式接入正文和参考资料，生成或维护来源配置。 |
| `analyze` | `analyze ./书名` | 分批精读并建立详细资料库，可追加章节范围或关注点。 |
| `resume` | `resume ./书名` | 读取保存的进度和纠错，恢复未完成任务。 |

例如，在 Claude Code 中显式接入书籍：

```text
/novel-research init ./书名
```

在 Codex 中使用同一动作：

```text
$novel-research init ./书名
```

`./书名` 表示当前项目下的书籍目录，也可使用完整路径。目录在当前会话中已明确时可省略；新会话应提供书籍目录。日常提问和流程命令都会执行必要的资料检查，尚未准备的来源由 Agent 接入。

### 原文查证与专题研究

可直接研究人物动机、认知变化、关系、时间线、伏笔和叙述方式。例如：

> 只依据第一至二十章，梳理主角从怀疑到确认身份的过程，分别列出证据、替代解释和未决问题。

> 对照这篇读者评论与正文，说明哪些判断有原文支持，哪些属于文学解释。

关键判断会回查原文，支持证据与反面证据分别记录。章节导航和旧报告用于定位材料，引用校验用于确认出处；对人物动机或文学意义的判断仍需结合上下文讨论。

### 全书精读与资料库

全书建库先逐批阅读正文，保存章节记录、人物称谓、认知变化与待核查线索，再按研究目标组织跨章专题。可在请求中明确关注点：

> 使用 novel-research analyze「./书名」，重点记录人物认知变化、事件时间线和伏笔候选；完成分批阅读后做跨章复核，保存原文出处、专题报告与未决问题。

精读使用宿主当前模型的用量，长篇可能需要多次会话。已接收的兼容任务可复用，后续会话可从保存的进度继续。完成阅读任务表示相应输入已处理，后续研究仍可补充遗漏或修正解释。

## 更新资料与持续研究

**新增文件与子目录**：将资料放入同一书籍工作区。Agent 会在开始、恢复研究或收到更新资料请求时，递归检查并接入新增内容；子目录中的资料也在检查范围内。可直接提出“接入新增资料并继续研究”。已有正文变更后，会更新来源版本，保留历史引用。增量接收沿用现有配置与记录，由研究任务触发执行。

**新增书籍工作区**：每部小说使用独立目录。研究另一部小说时提供新的目录路径，由 Agent 接入对应资料。

**新增链接**：提供链接及用途，例如“将这篇访谈作为补充资料保存并注明出处”。Agent 归档实际取得的内容；无法读取或仅取得部分内容时，会说明处理范围。

**保存与纠错**：研究过程中可要求保存发现、假设、偏好或更正。Agent 会核对相关原文，区分已查证判断与待核查意见，并记录有效的阶段进度。持久化内容是研究要点，完整聊天不会自动逐句存档。

**跨会话继续**：向新会话提供同一书籍目录，使用 `resume` 并补充当前目标。进度和研究记录保存在工作区中，可供兼容宿主继续使用。

## 结果与备份

下列路径均相对于书籍工作区：

| 内容 | 位置 |
| --- | --- |
| 来源配置，由 Agent 维护 | `novel.json` |
| 资料库入口 | `analysis/views/library.md` |
| 章节、人物称谓和疑问导航 | `analysis/views/` |
| 专题研究报告 | `research/<主题>/report.md` |
| 讨论要点、纠错与进度记录 | `notes/` |

资料库与导航在完成相应分析后生成。需要报告时，可在研究请求中指定输出目标。

备份或迁移时复制整个书籍工作区，包括原始资料、配置、研究记录和隐藏目录 `.novel/`。升级技能时替换技能安装文件夹，保留书籍工作区；升级后可要求 Agent 进行环境与资料检查。

## 示例与参考文档

| 资源 | 用途 |
| --- | --- |
| [示例书籍《渡灯记》](skills/novel-research/assets/example) | 六章原创正文与一份读者评论，可复制到独立工作区，体验资料接入、查证与建库流程。 |
| [安装与排障](skills/novel-research/references/setup.md) | 环境检查、安装步骤、编码及常见执行问题。 |
| [资料接收与更新](skills/novel-research/references/intake.md) | 本地文件、EPUB 和参考链接的接入规则。 |
| [全书资料库](skills/novel-research/references/library.md) | 分批精读、跨章复核和任务恢复流程。 |
| [研究与讨论记录](skills/novel-research/references/research.md) | 专题报告、研究记忆和纠错的保存方式。 |

示例资料用于体验工作流，参考文档提供进阶操作与流程细节。日常使用以书籍目录和研究目标作为输入，由 Agent 执行相应工具。

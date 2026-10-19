# 安装、准备与样例演练

## 普通使用：给 Agent 一个目录

安装并启用技能后，提供书籍目录，以自然语言提出研究问题。Agent 会执行必要的资料接入、检索和查证。可选流程入口为：`help` 展示帮助；`init <目录>` 显式接入资料；`analyze [目录]` 分批精读；`resume [目录]` 读取进度并恢复。

项目级安装后，在宿主中打开对应项目，为每本书建立独立资料目录。原文件可放在该目录任意层级，由 Agent 接收并建立来源配置。

Codex CLI/IDE 的示例为 `$novel-research init ./书名`，Claude Code 为 `/novel-research init ./书名`。本技能声明 `metadata.opencode/slash: "true"`，支持该字段的 OpenCode 版本也可使用 `/novel-research init ./书名`。其他宿主可加载技能后发送“使用 novel-research init ./书名”。依据 [Codex 技能说明](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills)、[Claude Code 参数说明](https://code.claude.com/docs/en/skills#pass-arguments-to-skills) 和 [OpenCode 元数据说明](https://opencode.ai/docs/skills/#write-frontmatter)。

动作参数由 Agent 理解并路由现有工具，不是 Python CLI 的子命令。目录在当前会话中已明确时可省略；新会话需指定目录。只请求 `help` 时不执行接入或分析；自然语言研究和流程入口都执行必要的环境、资料检查。`init` 是可选的显式接入方式。

以后只需把 EPUB / TXT / Markdown 放进这个目录或其新子目录，开始、恢复研究或收到更新资料请求时，Agent 会先轻量扫描未登记文件，按既有配置增量接收。普通未加密文字 EPUB 由内置标准库工具转换，不另装软件。提供访谈、评论等参考链接也可以。Agent 阅读必要内容、判断来源分类和正文顺序、维护配置，只在首次或来源改变时准备快照；只在会影响研究的实质歧义处询问。链接能否读取取决于宿主已有浏览能力，打不开的只记录为待获取。

这些步骤由 Agent 在执行任务时完成，不是后台监视或无人值守同步。缺失文件、扫描不完整、未取得的网页或准备失败会明确说明，不能当作已经入库。具体接收规则见 [目录、文件与参考链接](intake.md)。首次使用还需完成下方安装与样例检查。

## 运行前提

需要已经能正常工作的 Codex、Claude Code、DeepSeek Harness、OpenCode、WorkBuddy 或其他本地 Agent；宿主必须能读本地文件、执行 Python、写入指定小说目录。使用该宿主当前会话的模型，不配置本项目的 API Key。

工具要求 Python 3.11 或更高版本，只使用标准库，无需 pip、虚拟环境、Node、数据库服务或 GPU。本项目不替宿主安装软件或更改系统设置。提供 macOS 与 Windows 的安装方式；目前已在 macOS 环境跑通，Windows 实机兼容性仍待确认。安装后先做下方环境检查和样例演练，确认你的宿主能读取文件、执行工具并保存结果。

从 [Python 官方下载页](https://www.python.org/downloads/) 安装适合本机的 Python。macOS 安装后重新打开终端，检查：

```sh
python3 --version
python3 -c "import sys; print(sys.executable)"
```

Windows 可用已安装的启动器选择 Python 3：

```powershell
py -3 --version
py -3 -c "import sys; print(sys.executable)"
```

不同 Windows 安装方式的命令可能不同，按 [Python 官方 Windows 说明](https://docs.python.org/3/using/windows.html) 检查。没有 `py` 时，可用实际 `python.exe` 的绝对路径；PowerShell 调用带引号的可执行路径要加 `&`：

```powershell
& "C:\Users\me\AppData\Local\Programs\Python\Python311\python.exe" --version
```

版本至少为 3.11，后续始终使用检查通过的同一个解释器。脚本路径与小说路径是两个独立参数，两者都加引号。

## 安装完整 skill 文件夹

从项目 `skills/novel-research` 或分发包 `dist/novel-research.zip` 取出整个 `novel-research` 文件夹。保留 `SKILL.md`、`scripts`、`references` 和 `assets`，不要只复制入口文档，也不要额外套一层同名目录。

| 宿主 | 用户级安装位置 | 项目级安装位置 |
| --- | --- | --- |
| Codex | `~/.agents/skills/novel-research` | `<项目>/.agents/skills/novel-research` |
| Claude Code | `~/.claude/skills/novel-research` | `<项目>/.claude/skills/novel-research` |
| DeepSeek Harness | `~/.agents/skills/novel-research` 或 `~/.dsh/skills/novel-research` | `<项目>/.agents/skills/novel-research` 或 `<项目>/.dsh/skills/novel-research` |
| OpenCode | `~/.agents/skills/novel-research` 或 `~/.config/opencode/skills/novel-research` | `<项目>/.opencode/skills/novel-research` 或 `<项目>/.agents/skills/novel-research` |
| WorkBuddy（代码开发模式） | `~/.codebuddy/skills/novel-research` | `<项目>/.codebuddy/skills/novel-research` |

`~` 指当前用户主目录，如 macOS 的 `/Users/me` 或 Windows 的 `C:\Users\me`。复制普通文件夹即可，不需要符号链接或管理员权限。用户级用于多本书和多个项目，项目级随该项目使用；避免同时安装多个同名副本。

项目级安装后，在宿主中打开对应项目，刷新或新建会话再使用。下文终端示例采用用户级安装路径；项目级使用时，把脚本路径换成该项目中 `novel-research/scripts/novel.py` 的真实绝对路径。书籍目录仍由你单独指定。

上述发现规则依据 [Codex 官方 skills 文档](https://learn.chatgpt.com/docs/build-skills)、[Claude Code 官方 skills 文档](https://code.claude.com/docs/en/skills) 和 [DSH 文件系统 provider 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)。DSH 需要已启用 skills registry 与 filesystem provider；自定义 `DSH_HOME`、`DSH_AGENTS_HOME` 或扫描目录时按宿主配置安装。不要为了本 skill 擅自修改整套宿主配置。

`.dsh` 是 DeepSeek Harness 官方目录，不来自对简称的猜测；`~/.dsh` 在用户目录内，不能写成根目录 `/.dsh`。OpenCode 的路径依据 [官方技能说明](https://docs.opencode.ai/docs/skills/)，不要求为普通技能配置专门的 slash 命令。

WorkBuddy 的文件夹安装用于代码开发模式，依据 [项目级配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project) 与 [用户级配置兼容说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Setting)。完整技能 ZIP 也可通过“专家·技能·连接器 → 技能 → 添加技能 → 上传技能”导入，见 [技能界面说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。以上说明是宿主官方提供的加载方式；安装后仍需用实际宿主完成下面的演练。

在宿主中确认实际加载：Codex 用 `$novel-research` 或 skills 列表；Claude Code 用 `/novel-research`；DSH 查看技能目录并调用其加载工具。若未出现，检查 `SKILL.md` 的层级、同名副本、项目范围和宿主设置，再刷新或重启会话。其他宿主可以明确要求读取该文件的绝对路径；读得到文档还不够，必须继续完成脚本演练。

## 可选：用原创样例体验

已有自己的小说时，可以直接看 [准备自己的小说](#准备自己的小说)，不必复制或修改样例。

把 skill 下 `assets/example` 的完整内容复制到一个独立可写目录，例如 `/Users/me/Documents/小说 研究/渡灯记` 或 `D:\小说 研究\渡灯记`。新目录直接包含 `novel.json` 和 `sources`。不要把安装包里的样例目录当成长期数据目录。

样例《渡灯记》为本项目原创：六章正文、一份读者评论；配置明确给出顺序、UTF-8 编码和来源分类。

导入后保留 `novel.json`；它是来源登记清单，不是一次性缓存。开始自己的书时另建目录，让 Agent 建立新配置，不清空或沿用样例配置，也不把不同小说混进同一工作区。

样例的正文在 `sources/text`，评论在 `sources/supplements`；这是方便演练的布局，不是用户必须遵守的目录规范。自己的资料可以保留任意层级，由 Agent 发现并登记。

macOS 示例（将 `me` 替换为实际用户名）：

```sh
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" doctor
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" --workspace "/Users/me/Documents/小说 研究/渡灯记" doctor
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" --workspace "/Users/me/Documents/小说 研究/渡灯记" sources scan --new-only --limit 100 --offset 0
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" --workspace "/Users/me/Documents/小说 研究/渡灯记" prepare
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" --workspace "/Users/me/Documents/小说 研究/渡灯记" status
python3 "/Users/me/.agents/skills/novel-research/scripts/novel.py" --workspace "/Users/me/Documents/小说 研究/渡灯记" search "沈砚" "灯还亮着。"
```

Windows PowerShell 示例（Claude Code 安装路径同理换成 `.claude\skills`）：

```powershell
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" doctor
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" --workspace "D:\小说 研究\渡灯记" doctor
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" --workspace "D:\小说 研究\渡灯记" sources scan --new-only --limit 100 --offset 0
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" --workspace "D:\小说 研究\渡灯记" prepare
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" --workspace "D:\小说 研究\渡灯记" status
py -3 "C:\Users\me\.agents\skills\novel-research\scripts\novel.py" --workspace "D:\小说 研究\渡灯记" search "沈砚" "灯还亮着。"
```

预期：`doctor` 返回解释器和逐项检查结果；样例已有七个登记来源，轻量扫描的 `files` 为空、`registered_count` 为 7，无需重复添加；首次 `prepare` 导入六章正文，评论保留独立来源性质；`status` 返回当前版本与未完成分析。检索能定位两字人名与重复台词，并给出片段 ID、范围、命中总数和分页信息。成功输出为 `{"ok": true, "result": …}`，失败为 `{"ok": false, "error": …}` 且退出码非零；先检查 `ok` 再用 `result`。出现错误时先处理错误，不继续声称后续已完成。

下面的命令片段继续接在同一个解释器、脚本和 `--workspace` 前缀之后；尖括号内容从真实返回值替换：

```text
read --span "<检索返回的span_id>" --limit 3000
search "怀疑" "知道" "确认" --chapter-from 1 --chapter-to 5
search "许棠" --in supplements --kind commentary
task next --chapter-from 1 --chapter-to 1
```

让宿主依据 [分析格式](analyze.md) 阅读任务包，写入 `<小说目录>/.novel/inbox/analysis.json`，执行：

```text
task submit "<小说目录>/.novel/inbox/analysis.json"
status
```

预期：第一章对应任务提交成功，覆盖数增加；新会话再领取时跳过已接收的兼容任务。短句“灯还亮着。”在同一段出现两次，作为证据提交会产生歧义；选择包含说话语境的较长连续引文后再提交。搜索命中不等于引用已经通过校验。

再向宿主提出：“沈砚什么时候确认阿棠身份？第三章之前能否说他已经知道？请核对评论和正文的差异。”宿主可直接检索并读取第二、第五章，无需先分析其他章节。预期区分早期怀疑、后期确认和评论推测，并附真实出处。

依 [研究格式](research.md) 把需要继续使用的结果写入 `.novel/inbox/identity.json`，执行：

```text
research save "<小说目录>/.novel/inbox/identity.json"
research render "<保存返回的case_id>"
notes save "<小说目录>/.novel/inbox/correction.json"
notes recall --query "身份" --limit 10
check
```

研究文件和纠错文件必须由宿主根据真实查询结果填写，完整格式见 [研究与讨论记录](research.md)。预期生成带快照引文的报告、查回来源明确的纠错，并通过现有记录检查。换新会话后只提供小说路径和问题，检查能否查回有效纠错、研究范围与下一步；这一步验证宿主使用流程，不能由 `doctor` 代替。

## 准备自己的小说

让 Agent 把用户给出的目录作为工作区，保留 TXT / Markdown 的原位置。每次新任务或恢复工作先运行 `sources scan --new-only`，按 `next_offset` 看完新增项的所有页，并检查全局 `scan_complete`、`missing` 与 `errors`。Agent 根据内容和用户背景分类、编排顺序，再登记，例如：

```text
sources scan --new-only --limit 100 --offset 0
sources add "原有目录/上册.txt" "原有目录/下册.md" --kind text --title "我的小说"
sources add "参考资料/读者评论.md" --kind commentary
prepare
status
```

以上为首次接收示例。无新增文件时也要看 `status`：已有来源改变则 `prepare`，尚未准备则初始化；已有快照且来源未变无需重复准备。不带 `--new-only` 可全量盘点。普通用户无需理解这些命令或 JSON；新增正文可用 `--before` / `--after` 插入已知位置，不静默覆盖已有分类和顺序。扫描不完整或有缺失来源时，不能声称已全部同步。详情见 [接收资料](intake.md)。

如果提供的是 EPUB，Agent 按 [EPUB 接收流程](intake.md) 自动执行转换，再登记返回的派生文本：

```text
epub convert "原书/这本书.epub"
sources add "<转换返回的output_path>" --kind text
prepare
status --chapters
```

正文不能提取或仅有部分内容时会明确说明；图片不做 OCR，不处理受保护正文或固定版式。原包保留，派生文本及转换映射供检索定位，不能把位置当作原书页码。换版先重新转换；派生文本有手动编辑时工具拒绝覆盖，先保留编辑再核对版本。

首次长期研究可对 Agent 说：“先分批精读全书，建立详尽资料库，再做跨章复核；中断时保存断点。”按 [全书资料库流程](library.md) 执行；也可以直接提问。生成的 `analysis/views/library.md` 为入口，章卡、人物称谓和疑问帮助定位，专题报告保存在 `research/`。精读消耗当前宿主模型的用量，不在后台调用额外模型。

## 高级：手动配置与目录排障

下面仅供需要直接检查或修正配置时使用。正常增加资料由 Agent 通过 `sources add` 维护 `novel.json`。手动配置的正文顺序按 `sources` 中对应条目确定；不能因文件名叫“官方设定”就当作作者资料。

```json
{
  "format_version": 1,
  "title": "我的小说",
  "sources": [
    {"path": "sources/text/上册.txt", "kind": "text", "encoding": "utf-8"},
    {"path": "sources/text/下册.md", "kind": "text", "encoding": "utf-8"},
    {"path": "sources/supplements/读者评论.md", "kind": "commentary", "encoding": "utf-8"}
  ]
}
```

允许的来源类型为 `text`、`supplement`、`author_note`、`interview`、`commentary`、`user_note`。不明性质的补充材料用 `supplement`。路径相对小说工作区，不允许越界路径。

默认严格读取 UTF-8 / UTF-8 BOM。失败时确认实际编码，例如登记新资料时用 `sources add "相对路径.txt" --kind text --encoding gb18030`，或检查现有配置中的 `"encoding": "gb18030"`。旧的 `prepare --source "…" --encoding gb18030` 仍可用于首次建立最小配置；已有配置时用登记命令或显式修正配置。不要忽略错误、替换乱码或修改原始小说来让导入通过。

准备后检查章节预览。TXT 识别独立的“第一章 标题”等常见章节行，Markdown 按章节标题层级识别；无标题、重复编号或前言可能需要确认。原文仍可检索，但未确认目录不能冒充完成章级分析。如需修正目录，在相应 source 配置 `chapters: [{"title": "…", "start": 0, "end": 123}, …]`；范围必须连续覆盖整个解码文本。由程序计算 Unicode 字符位置并展示目录供用户核对，不要求模型手算，也不改写原文来凑目录。

正文或配置改变后重新 `prepare`，再运行 `status` 和 `check`。旧记录保留，受影响任务会变为过期；先前报告仍指向其旧快照。

## 排障与升级

| 现象 | 处理 |
| --- | --- |
| 找不到 `python3` / `py` | 重新打开终端，确认已安装 Python；用实际解释器绝对路径运行。 |
| 版本低于 3.11 | 安装合适版本，检查 `sys.executable`，让宿主使用同一个解释器。 |
| 终端中文乱码 | 文件仍保持 UTF-8；可给解释器加 `-X utf8`，并检查终端的 UTF-8 显示设置。不要为显示问题重写原文。 |
| 解码失败 | 核实来源文件编码后显式配置，保留原始字节；禁止容错替换后继续。 |
| 工作区不可写或替换失败 | 用带工作区的 `doctor` 检查，换到有权限的本地目录或修复该目录权限。 |
| 手动命令可用，宿主不能执行 | 查看宿主的文件访问、执行权限与项目范围；按宿主提示授权需要的本地操作，不关闭整套保护。 |
| 工作区忙 / 遗留写锁 | 读取错误给出的 `.novel/write.lock/owner.json`，先确认对应进程和其他宿主已停止写入；确认确为遗留后只移除该锁目录，再重试。不要按锁的年龄自动删除。 |
| 产物过期或来源已变 | 先核对修改是否有意，重新 `prepare` 后 `check`；按当前任务继续，不能强改旧记录版本号。 |
| 引文不唯一或 ID 不存在 | 重新 `read`，扩大连续引文；不得编造位置或复用另一书籍的 ID。 |
| 正式记录损坏 / 未知格式 | 保留原文件，停止相关写入，恢复备份或使用兼容版本；不要把删掉记录当成修复。 |

备份整个小说工作区，包括原始来源、`novel.json`、`.novel/snapshots`、分析、研究、讨论记录和手写笔记。升级时替换 skill 文件夹，数据目录保留；先备份、运行 `doctor` 与 `check`。未知格式不静默迁移，须按后续版本的明确迁移说明处理。

同一工作区只使用一个写入者。当前范围为本机普通文件系统，不把同步盘上的多机同时写入视为支持场景。

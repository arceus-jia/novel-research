# 按问题研究与保留讨论

## 从问题出发

先明确用户问的是原文定位、小说事实、人物认知、跨章推断还是文学解释。简单问题可以直接检索、核对上下文并回答；不要求先建立主题或完成全书分析。对需要延续的调查，保存一个研究主题，固定来源版本与范围。

新会话先 `notes recall --limit 20`，再按问题 `notes recall --query "身份"`。结合 `status` 和工作区 `research` 中的相关主题，读当前结果、未决项和明确关联的纠错；不要把全部旧讨论重新塞入上下文。发现报告标记需要复核时先查目标与原文，再生成新结果。

默认从正文查证：

```text
search "阿棠" "许棠" --in text --chapter-from 1 --chapter-to 5 --limit 20
read --span "<命中返回的span_id>" --limit 3000
search "怀疑" "不知道" "确认" --in text
search "身份" --in analysis
search "许棠" --in supplements --kind commentary
```

这些命令前都加同一本书的脚本与 `--workspace` 前缀。`search` 是字面匹配，可给出多个查询表达。先读返回的范围、总数和截断标记，结果太多时用 `--offset` 翻页；上下文用 `read --limit … --offset …` 继续读取。章卡和笔记命中是导航或二手材料，不能代替正文引用。

`search --in analysis` 默认检索粗读和精读的当前记录，命中标注 `mode`；可加 `--mode skim` 或 `--mode analyze` 限定阅读模式。粗读梗概用于定位线索，精读记录也需按引文核对；不能把粗读覆盖当作精读完成。已有纠错或讨论与正文出入时，读取相关片段及前后文，在回答中给出原句和章节出处，再保存实际更正。

深层动机、伏笔或身份研究应查支持证据、反面证据和有实质区别的替代解释。例：早期伤疤可支持“怀疑身份”，后期明确承认可支持“确认身份”，不能将后文揭示倒灌为角色早已知道。没有反证命中时记录实际查过的表达与范围，不能写成“全书不存在反证”。

允许没有唯一答案。人物言语、叙述事实和研究者解读分别呈现；解释可并存，不用虚构百分比表示可靠性。

## 保存研究结果

宿主在工作区 `.novel/inbox` 写 JSON，再运行 `research save "<结果文件>"`。最小实用格式如下；ID 和引文必须从本次 `read` 返回值替换：

```json
{
  "case_id": "identity",
  "question": "沈砚在第二章是否已经知道阿棠的身份？",
  "scope": {"chapter_from": 1, "chapter_to": 2, "source_kinds": ["text"]},
  "findings": [
    {
      "kind": "belief",
      "text": "当前正文明确写的是怀疑，尚未确认身份。",
      "evidence": [{"span_id": "从read返回值复制", "quote": "沈砚开始怀疑阿棠就是许棠，但此时他并不知道她的身份。"}],
      "counter_evidence": []
    }
  ],
  "queries": ["怀疑；不知道；身份（正文第一至第二章）"],
  "hypotheses": ["伤疤促使沈砚怀疑，但仅靠伤疤不足以确认。"],
  "unresolved": ["身份在何时得到明确确认，需要继续查后文。"]
}
```

`question`、`findings` 必填，`findings` 可以是空数组。`scope` 可省略，默认正文；推荐保存实际章节与来源范围。`queries` 记录可核查的查询与覆盖范围；`hypotheses` 和 `unresolved` 是字符串数组，用于保留未证实想法与下一步，不代表事实记录。需要原文反证时，在相应 `counter_evidence` 放同样的 `span_id` / `quote` 对象。

章节上下限只约束正文。对照前两章与读者评论时，可使用 `{"chapter_from":1,"chapter_to":2,"source_kinds":["text","commentary"]}`：正文引用仍须位于前两章，评论须显式纳入来源类型，但没有正文章号。检索补充资料时单独查询，不叠加正文的章节过滤；评论可能谈到后文，应交代其范围，不能当作限定章节的正文事实。

`kind` 使用 `narration`、`speech`、`belief`、`inference`、`interpretation`、`unknown`。前三类必须有证据；后三类缺证据时仅为草稿，不能在后续回答中升格成有依据的事实。所有提供的证据仍须来源校验。具体类型含义见 [分析说明](analyze.md)。

可选 `source_revision`、`workspace_id` 用于明确绑定已获取的版本与书籍；不要自造值。可选 `id` 用于显式重试同一记录；不提供时由工具生成。保存返回成功后运行：

```text
research render identity
```

工具生成 `research/identity/report.md`，从快照提取引文。报告是可重建视图，不直接编辑成另一套事实库；修正研究时保存新结果。同一主题固定一个来源版本，可以通过后续记录扩大范围；换正文版本后复核并建立适合新版本的调查，不能混用片段。

工具检查的是结构、来源和定位，不会证明引文一定支持判断。正式报告以外的聊天回答也应给出关键出处，但不声称每一句都经过程序校验。

## 讨论记忆与纠错

只保存对后续有价值的要点，不保存完整聊天或内部推理。用户明确要求记住时直接保存；形成实质更正、发现、假设排除和进度时主动保存。用户要求不保存时遵从。用户观点不能标成原著事实，也不能把 Agent 总结标为用户认可。

宿主写入 `.novel/inbox/correction.json` 后调用 `notes save "<记录文件>"`。未核实的用户纠错可立即保存为：

```json
{
  "type": "correction",
  "content": "用户指出：此前回答把第二章的怀疑写成了知道，应回查原文后更正。",
  "origin": "user",
  "status": "pending",
  "case_id": "identity"
}
```

查原文并完成语义判断后，以有证据的记录保存实际更正：

```json
{
  "type": "correction",
  "content": "已核对第二章，正文明确区分怀疑与知道；原来的已知身份判断应更正为此时仅怀疑。",
  "origin": "agent",
  "status": "applied",
  "target_id": "需要更正的已有记录ID",
  "case_id": "identity",
  "evidence": [{"span_id": "从read返回值复制", "quote": "沈砚开始怀疑阿棠就是许棠，但此时他并不知道她的身份。"}]
}
```

`target_id` 必须是真实存在且属于本书的记录。纠正尚未存档的聊天回答时可省略目标，在 `content` 写清问题、对象和必要短摘；不要先把错误存为正式结论。`applied` 需要明确申请且附有效证据，无证据更正只能为 `pending`。它记录 Agent 已作出语义复核决定，引用校验本身不保证该决定正确。无法核实或仍有分歧时保留争议，可以按该假设继续调查。

记录类型与来源：

| 字段 | 允许值与用途 |
| --- | --- |
| `type` | `correction` 更正、`hypothesis` 假设、`finding` 发现、`preference` 本书偏好、`checkpoint` 进度、`withdrawal` 撤回 |
| `origin` | `user` 用户表达、`agent` Agent 判断、`discussion` 讨论要点；不等于被用户认可 |
| `content` | 简短、足以继续使用的要点；更正包含理由 |
| `evidence` | 可选正文 `text` 引文；提供后必须通过校验；补充资料证据放入对应研究主题 |
| `target_id` | 更正的已有目标；撤回时必填已有讨论记录 ID |
| `case_id` | 可选已保存研究主题的 ID；尚未创建主题时省略 |
| `status` | 更正使用 `pending` 或 `applied`；不要自行填写派生状态 |

阶段进度可以保存为 `{"type":"checkpoint","origin":"agent","content":"已查正文第一至第二章；仅支持怀疑身份。下一步查第五章身份揭示，并核对评论。","case_id":"identity"}`。偏好只作用于本书，不更改全局 skill 或小说事实。

收到工具成功结果后才向用户说已保存。失败时说明未落盘，保留草稿；不要在会话结束时笼统声称“以后会记得”。

## 查回、更正与撤回后的视图

```text
notes recall --query "身份" --limit 10
notes recall --query "身份" --history --limit 30
```

默认查回当前相关条目，显示 `pending`、`applied`、`disputed`、`needs_review`、`stale` 等有效状态，按状态使用；已被替代的 `superseded` 和已撤回的 `withdrawn` 条目使用 `--history` 查看。更正不覆盖不可变原记录，明确关联的研究或视图会标记需复核。首版只跟踪显式关系，不保证识别所有隐式影响。

撤回时保存 `{"type":"withdrawal","origin":"user","content":"用户撤回此前这条意见，相关结论需要重新查证。","target_id":"真实讨论记录ID"}`。撤回更正后不能静默把原判断恢复成定论，应按剩余证据复核。正文变更使证据型判断过期时重新核对，不涉及正文的偏好可以继续使用。

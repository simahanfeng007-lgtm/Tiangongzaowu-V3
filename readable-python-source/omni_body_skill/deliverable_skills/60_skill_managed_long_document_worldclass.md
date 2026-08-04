# 受管超长文档工程 Skill

## 适用边界

只在用户明确要求超长、多章、持续编写、可断点恢复的文档工程时使用。普通商业方案、短报告和一次性 Word 文档继续使用商业 Word Skill，不得因为出现 `Word` 或 `docx` 就升级为受管工程。

## 第一原则

聊天记录不是超长文档的权威状态。规划、章节源文件、引用索引、校验哈希和断点必须写入用户工作区；上下文压缩后只能从这些已核验文件恢复。工具负责原子读写、格式化、质检和打包，模型负责内容判断与写作。

## 标准目录

```text
文档项目/
  project_manifest.json
  规划/
    目标与读者.md
    总大纲.md
    章节清单.json
  章节/
    001-章节名.md
  资料/
    source_index.json
  状态/
    checkpoint.json
    rolling_summary.md
    hashes.json
  交付/
    完整正文.md
    最终文档.docx
    qc-report.json
```

`project_manifest.json` 至少包含项目标题、目标读者、`target_words`、章节数、当前版本，以及按最终顺序排列的 `chapter_files` 项目内相对路径数组。`checkpoint.json` 至少包含当前章节、最近完成章节、下一步、未解决问题、源文件哈希和更新时间。

## 必须执行的循环

1. 先用 `file.list` 检查工作区及候选项目文件夹。
2. 若存在 `project_manifest.json`，必须用 `file.read` 核验 manifest、总大纲、章节清单和 checkpoint；缺失或损坏时进入修复，禁止声称“继续成功”。
3. 若不存在完整工程，先用 `file.mkdir` 和 `file.write` 创建标准目录、规划和初始断点。不得把聊天中的临时大纲当成持久规划。
4. 按章节清单逐段生成。每次只加载当前章节需要的总大纲切片、上一章节摘要和相关资料，不把整本历史重新塞回上下文。
5. 每完成一个有意义的章节批次，先落盘章节，再更新 `rolling_summary.md`、`checkpoint.json` 和 `hashes.json`。写入成功前不得推进断点。
6. 接近宿主上下文预算时，先完成第 5 步；压缩后重新读取 manifest、checkpoint、当前章节源文件和相关哈希，再继续。不得依赖被压缩掉的工具回显。
7. 汇编前按章节清单验证章节无缺失、无重复、顺序正确，使用 `docx.create(args.source=完整正文.md)` 生成 Word；超长正文不得作为单个内联 `args.content` 重传。
8. 运行 `qc.docx.delivery_check(args.document_type=long_document, args.project_manifest=project_manifest.json)` 和 `qc.writing.ai_tone_check`。超长文档门会从 manifest 读取目标字数和章节源文件并核验真实完整度；任何质量门失败都要修复源 Markdown、重新生成 Word、再次质检。
9. 最终用 `deliverable.package` 同时打包完整工程文件夹、最终 Word、源 Markdown 和 QC 报告。只生成了文件名、空 ZIP、聊天摘要或局部章节都不算完成。

## 断点与恢复规则

- 断点只描述已经成功落盘并可通过哈希核验的状态。
- checkpoint 指向的章节不存在、哈希不一致、章节清单出现断号时，必须停在恢复阶段。
- 正常完成后保留最终规划、源章节、rolling summary、最终 checkpoint、哈希、QC 报告和交付物；原始工具输出、临时提示词、重复中间摘要不进入项目文件夹。
- 续写不得覆盖既有章节；修订必须先保存可回滚版本或使用精确补丁，并更新版本与哈希。

## 完成门

只有同时满足以下条件才可对用户宣称完成：

- manifest、总大纲、章节清单和 checkpoint 可解析且互相一致；
- 所有计划章节源文件存在，章节编号无缺失和重复；
- checkpoint 与当前源文件哈希一致；
- 最终 DOCX 实际存在、可打开，且 Word QC 通过；
- 交付 ZIP 实际存在、非空并包含完整项目文件夹、最终 DOCX、完整正文和 QC 报告。

最终回复应给出完整项目文件夹与最终正文/Word 的工作区相对路径，不以过程日志代替交付。

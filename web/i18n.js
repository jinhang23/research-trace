/* i18n.js — 网页界面文案的中英对照表。
 *
 * 范围只有**界面文案**：按钮、标签、提示、toast、对话框、可溯源等级说明。
 * 不含 MCP 工具描述、Python 侧的异常信息、FORMAT.md / README.md 的正文——
 * 那些的读者是 agent 和维护者，不是这个页面。
 *
 * 零依赖、零构建：静态导出要能 file:// 断网打开，所以这里不引 md.js、不引任何
 * 别的东西（连 esc 都自己写一份，见下）。载入顺序上它必须排在 app.js **之前**。
 *
 * ---------------------------------------------------------------- 两条硬约定
 *
 * 【一】t() 返回**纯文本**，tHtml() 返回**HTML**。按目的地选，不要混用。
 *
 *   t(key, vars)      → 纯文本。变量原样插入，不转义。
 *                       用在 textContent / title= / placeholder= / alert /
 *                       prompt / confirm / toast()——这些地方浏览器自己会把
 *                       文本当文本，再转义一遍只会让人看见 &amp;quot;。
 *   tHtml(key, vars)  → HTML 片段。先把整条文案转义，再展开一小撮行内标记
 *                       （**粗体** · `代码` · 换行 → <br>），最后插入变量，
 *                       变量默认转义。用在 innerHTML 模板里。
 *
 *   **绝对不要把 t() 的结果拼进 innerHTML。** 那是唯一能把用户数据（步骤标题、
 *   搜索词、服务端错误原文）变成可执行 HTML 的路径。要拼就用 tHtml()。
 *   为了让这条约定可查而不只是口头，STRINGS 表里**一个 HTML 标签都不许有**
 *   （tests/i18n.test.js 钉着这一条）：标记只能是 ** 和 ` 那两种，由 tHtml
 *   在转义之后才展开——于是表本身永远不可能是注入源。
 *
 *   变量要放原始 HTML 时（比如已经拼好的 <a>）显式写成 {link: {html: "<a …>"}}，
 *   这样"我这里放的是 HTML"是写在调用处的，不是猜出来的。
 *
 * 【二】t() 跟的是**界面语言**，正文模板跟的是**内容语言**。
 *
 *   `## 为什么` 那五行是要写进 note.md 的字，它属于记录本身，不属于界面：
 *   一个中文记录不该因为界面切成英文就长出英文小节标题（那会让 note.md 和
 *   note.en.md 的小节名对不上，评级和 check 就都找不到内容了）。
 *   所以模板走 tIn(contentLang, "template.body")，显式传语言，不读界面语言。
 *   小节名必须和 trace_core.SECTION_NAMES 逐字一致，那是一张封闭词表。
 *
 * ---------------------------------------------------------------- 缺 key 怎么办
 *
 * 缺 key 返回 key 本身（不崩），并在控制台 warn 一次。静默回退成中文或空串会让
 * 漏翻的地方永远发现不了——页面上看着有字，谁也不会去查。同理，某个语言缺这条
 * 但英文有时会回退到英文并单独 warn 一次：能用，但你会知道它缺了。
 */
(function (global) {
  "use strict";

  var STORE_KEY = "trace.lang";
  var EVENT = "tracelang";
  var DEFAULT = "en";

  /* ------------------------------------------------------------------ 文案表
   *
   * key 用点分层级，不用中文原文当 key：改一个字文案就断，而"改一个字"正是
   * 文案最常发生的事。层级按**界面上的区块**分，不按语法功能分——接线的人是
   * 对着屏幕找 key 的。
   *
   * 英文不是中文的机翻。这套中文文案有它的性格：它解释「为什么」，不复述操作
   * （"Deleting needs a reason" 是复述，"删了之后那一句话就是仅存的证据" 才是
   * 这个系统在说话）。英文要保住这一点——读它的是科研工作者，机翻腔会让整个
   * 系统显得廉价。
   *
   * 复数：值可以写成 {one: …, other: …}，t() 按 vars.n 选。中文不需要复数，
   * 写一条字符串就行；英文 "1 step / 3 steps" 这种差别在界面上很显眼。
   */
  var STRINGS = {

    /* ======================================================== English ==== */
    en: {
      /* ---- 顶栏 ---- */
      "app.proj.switch": "Switch project",
      "app.proj.all": "All projects ▸",
      "app.proj.option": "{name} ({n})",
      "app.search.placeholder": "Search id / title / body / tags   (press / to focus)",
      "app.scope.one": "This project",
      "app.scope.all": "All projects",
      "app.scope.one.title": "Searching this project only — click to search every project",
      "app.scope.all.title": "Searching every project — click to narrow it to this one",
      "app.view.graph": "Graph",
      "app.view.list": "List",
      "app.view.graph.title": "Graph view",
      "app.view.list.title": "List view",
      "app.view.flow": "Flow",
      "app.view.flow.title": "Data-flow view — edges are declared inputs, not the tree",
      "app.new": "＋ New step",
      "app.new.title": "Branch a new step off the selected one (n)",
      "app.newproj": "＋ Project",
      "app.newproj.title": "Start a new project",
      "app.token.title": "Set the write token",
      "app.token.set": "Write token is set — click to replace or clear it",
      "app.token.unset": "No write token — you can read, not write",
      "app.live": "Live connection",
      "app.live.static": "Static export — read-only",
      "app.lang.title": "Interface language",
      "lang.en": "English",
      "lang.zh": "中文",

      /* ---- 项目索引页 ---- */
      "home.new": "＋ New project",
      "home.empty": "No projects yet. Hit **＋ Project** in the top right to start one.",
      "home.nofilter": "No project is named anything like \"{q}\".\nMatches inside step bodies are in the search results above.",
      "home.filter.count": "{shown} / {total} projects",
      "home.card.meta": "{steps} · done {done} / wip {wip} / dead {dead}",
      "home.card.latest": "last {date}",
      "home.card.warnings": "⚠ {warnings}",

      /* ---- 图 / 列表视图 ---- */
      "list.empty": "No steps in this project yet. Hit **＋ New step** in the top right to begin.",
      "list.legend.done": "done — solid",
      "list.legend.wip": "wip — dashed",
      "list.legend.dead": "dead — dotted",
      "list.legend.l0": "chain broken here",
      "list.legend.reprofail": "reproduction failed",
      "list.zoom.out": "Zoom out",
      "list.zoom.in": "Zoom in",
      "list.zoom.reset": "Back to 100%",
      "list.zoom.fit": "Fit to width",
      "list.zoom.fit.label": "Fit",
      "list.mark.l0": "L0, not traceable — {why}",
      "list.mark.reprofail": "Reproduction failed",
      "list.mark.reprofail.note": "Reproduction failed: {note}",

      /* ---- 详情面板 ---- */
      "detail.meta.commit": "commit {commit}",
      "detail.meta.parent": "parent {id}",
      "detail.meta.code": "{kind} {loc}",
      "detail.meta.inputs": "inputs {ids}",
      "detail.act.edit": "✎ Edit body",
      "detail.act.delete": "Delete",
      "detail.act.delete.title": "Really deletes this step. Only for a mis-created step, test data, or a secret pasted in by mistake — an experiment that failed belongs in dead, not in the bin.",
      "detail.act.child": "＋ Branch from here",
      "detail.backlinks": "Referenced by",
      "detail.files": "Files · {n}",
      "detail.files.empty": "No attachments yet.",
      "detail.files.drop": "Drop logs, scripts or figures anywhere on this page and they upload, with a reference appended to the body. To put one in the middle of the text, switch to edit mode and paste the screenshot with Ctrl+V.",
      "detail.file.remove": "remove",

      /* ---- ① 移动审计：parent 可改，但改动本身要留在文件里 ---- */
      "move.act": "⇄ Move to another parent",
      "move.act.title": "The tree records how the thinking went, and sometimes the thinking got filed in the wrong place. Moving keeps the id and appends the old parent to the note — nothing is overwritten.",
      "move.badge": "moved",
      "move.badge.title": { one: "Where this step hangs has been changed {n} time. When, from where, and why are all in the note itself.",
                            other: "Where this step hangs has been changed {n} times. When, from where, and why are all in the note itself." },
      "move.head": "Move history · {n}",
      "move.entry": "{date} · moved from {from} to {to} — {reason} ({by})",
      "move.entry.nobody": "{date} · moved from {from} to {to} — {reason}",
      "move.from.root": "a root",
      "move.to.root": "a root of its own",
      "move.by.human": "by hand",
      "move.dialog.title": "Move {id} under a different parent",
      "move.dialog.hint": "`id` does not change and is never handed out twice — the `[[003b]]` in someone else's note and the footnote in a paper both keep working. What changes is where this step hangs, and the change itself is written into the note.",
      "move.field.parent": "New parent",
      "move.parent.none": "(none — make this a root)",
      "move.parent.current": "currently {id}",
      "move.field.reason": "Why",
      "move.reason.placeholder": "What was wrong about where it hung before?",
      "move.reason.required": "Required. Moving a step changes the shape of the record, and the shape is most of what a reader gets. This one line is the only thing that can say it used to be a different shape — and why the old one was wrong.",
      "move.submit": "Move it",
      "move.cancel": "Cancel",
      "move.err.reason": "A move needs a reason — that's the whole difference between moving and losing",
      "move.err.self": "{id} can't be its own parent.",
      "move.err.cycle": "Hanging {id} under {parent} would close a loop, and a loop has no first step to start reading from.",
      "move.err.descendant": "{parent} is downstream of {id}. A step can't hang under something that grew out of it.",
      "move.err.noop": "{id} already hangs off {parent} — there is nothing to record.",
      "move.err.project": "A step can only move inside its own project. Ids restart at 001 in every project, so across projects every `[[…]]` would become ambiguous.",
      "move.err.missing": "There is no step {parent} in this project.",

      /* ---- ② 数据依赖：树是记录，input 是字节 ---- */
      "input.lead": "**`parent` is the step I was thinking from. `input` is where the bytes came from.** Most of the time that's the same step; when it isn't, these lines are the only place that says so — a tree holds one parent, a pipeline can eat four artifacts at once.",
      "input.parent.tip": "The tree keeps one parent per step. Data flow is a graph, so it is recorded as inputs instead of bending the tree into one.",
      "input.head": "Artifacts this step consumed · {n}",
      "input.empty": "Nothing declared, which reads as \"the data came from the parent\". Declare an input when it didn't.",
      "input.entry": "from {link} — {what}",
      "input.entry.bare": "from {link}",
      "input.consumers.head": "Steps that consumed this one's output · {n}",
      "input.consumers.empty": "No step has declared this one's output as an input yet.",
      "input.warn.missing": "An input points at {id}, and there is no such step here. Deleted, or a typo — either way that dependency can no longer be followed.",
      "input.warn.self": "{id} declares itself as an input. A step can't consume what it hasn't produced yet.",
      "input.warn.cycle": "The data flow loops: {chain}. Something had to run first.",
      "flow.title": "Data flow",
      "flow.lead": "Every edge here is a declared input. The tree underneath is untouched — this is a second reading of the same files, not a second copy of them.",
      "flow.legend.tree": "parent — where the thinking came from",
      "flow.legend.data": "input — where the bytes came from",
      "flow.legend.both": "both at once — the parent is also the source of the data",
      "flow.empty": "No inputs declared in this project, so the data flow is exactly the tree.",

      /* ---- ⑤ 代码位置：commit 只是其中一种 ---- */
      "code.head": "Code · {n}",
      "code.kind.git": "git",
      "code.kind.snapshot": "snapshot",
      "code.kind.container": "container",
      "code.kind.git.title": "A commit in a repository — the history itself is the proof",
      "code.kind.snapshot.title": "A copy of the tree as it actually ran, with a checksum for every file",
      "code.kind.container.title": "An image digest — the code plus everything it needed to run",
      "code.manifest": "manifest {name}",
      "code.manifest.title": "The file that lists every file and its checksum. That list is what makes a directory as good as a commit.",
      "code.files": { one: "{n} file", other: "{n} files" },
      "code.empty": "No code location recorded.",
      "code.from.commit": "from the commit line",
      "code.from.commit.title": "Read off the commit line, not stored a second time. There is still exactly one copy of this fact in the file.",
      "code.l2.note": "The code is here, a checksum for every file is here — that is not worse than a commit. Plenty of real work runs out of a directory nobody ever put in git; refusing it L2 would only make the rating lie.",

      /* ---- ⑥ 提示级诊断：说清「这条不影响等级」 ---- */
      "lint.head": "Hints · {n}",
      "lint.badge": "hint",
      "lint.note": "Hints only. None of these change the level — they are things a later reader will wish you had written down.",
      "lint.level.error": "error",
      "lint.level.warn": "warning",
      "lint.level.hint": "hint",
      "lint.subheads": "`{section}` has no prose of its own, only sub-headings beneath it. That counts as written — this line is here so you know how it was read.",
      "lint.table.nodesc": "A table with nothing said about it. Someone handed only the numbers has to guess which direction is better.",
      "lint.pre.nodesc": "A code block with nothing said about it — say what it does, or what its output turned out to mean.",

      /* ---- 可溯源性 · L0–L4 ---- */
      "trace.title": "Traceability · L0–L4",
      "trace.self": "this step",
      "trace.chain": "the whole chain",
      "trace.weakest": "the weakest link is {link} {title} — mend that one first",
      "trace.weakest.self": "this step is the weakest link",
      "trace.ok": "Everything a machine can judge is here. Getting higher takes a person actually re-running it.",
      "trace.chip.title": "{level} {name} — {hint}",
      "trace.level.L0": "not traceable",
      "trace.level.L1": "readable",
      "trace.level.L2": "locatable",
      "trace.level.L3": "re-runnable",
      "trace.level.L4": "reproduced",
      "trace.level.L0.hint": "\"Why\" or \"What\" is empty, a figure has no caption, or a done/dead step has no conclusion",
      "trace.level.L1.hint": "You can follow the judgement that was made, but you can't run it",
      "trace.level.L2.hint": "The code and the artifacts can both be found again — a commit, or a snapshot directory with a checksum per file, or an image digest",
      "trace.level.L3.hint": "Someone has confirmed the command, the environment and the seed are all there",
      "trace.level.L4.hint": "Someone actually re-ran it and the numbers matched within tolerance",
      "trace.missing.why": "\"Why\" is missing — the one field nothing can generate for you",
      "trace.missing.what": "\"What\" is missing — re-running this depends on it",
      "trace.missing.conclusion": "\"Conclusion\" is missing — did the hypothesis hold or not?",
      "trace.missing.captions": "A figure with no caption — to a text-only reader that figure is a black hole",
      "trace.missing.commit": "No code location — a commit, a snapshot with per-file checksums, or an image digest; with none of them the code behind this result is gone",
      "trace.missing.paths": "No artifact locations — where the data and the weights live is now anybody's guess",
      "trace.repro.head": "Reproduction log · {n}",
      "trace.repro.empty": "Nobody has tried to reproduce this yet. `repro:` lines are written back by the audit and reproduce agents — a failed attempt is kept exactly like a successful one.",
      "trace.repro.verified": "reproduced",
      "trace.repro.runnable": "re-runnable",
      "trace.repro.failed": "reproduction failed",
      "trace.repro.unknown": "state unknown",

      /* ---- 项目洞察 ---- */
      "insight.title": "💡 {name} · Insights",
      "insight.lead": "Ideas · what worked and what didn't · the traps you already stepped in. It all lives in `project.md`, so `grep` still answers after every program here is gone.",
      "insight.empty": "No insights yet.\nThis is where judgements that **belong to no single step** go — \"back-translation never helped on this dataset\" is the verdict after three attempts, and hanging it on any one of them would be wrong.",
      "insight.edit": "✎ Edit insights",
      "insight.add": "＋ {label}",
      "insight.idea": "Ideas",
      "insight.works": "Works",
      "insight.fails": "Doesn't work",
      "insight.pitfall": "Pitfalls",
      "insight.idea.hint": "Not verified yet, but worth writing down",
      "insight.works.hint": "Tried it, it helps",
      "insight.fails.hint": "Tried it, it doesn't — worth exactly as much as what did",
      "insight.pitfall.hint": "Traps that bite again and again",
      "insight.prompt": "Note one \"{label}\" (one sentence; better with a number in it):",
      "insight.editor.title": "💡 Edit project insights",
      "insight.editor.hint": "Markdown. Only these four sections are submitted (plus whatever sits above the first heading). The `trace_insight` tool appends into the same sections — keep them here and what people and agents write lands in one place.",
      "insight.editor.others": "The sections below are not insights; this edit will not touch them",
      "insight.editor.others.note": "The reason written down when a step is deleted lands in `## 已删除` (\"Deleted\"). The directory is gone by then and those few lines are the only evidence left, which is why they stay out of this box.",
      "insight.editor.warn": "⚠ **{sections}** are not insight sections and will be dropped on save (the copy on disk stays exactly as it is).",
      /* ---- ④ 洞察的 id / 取代 ---- */
      "insight.id": "id {id}",
      "insight.id.title": "A stable handle for this one line. Later on you re-anchor or supersede it by this id — the sentence may be rewritten, the handle may not.",
      "insight.superseded": "superseded by {id}",
      "insight.supersedes": "supersedes {id}",
      "insight.badge.superseded": "superseded",
      "insight.superseded.title": "Kept on purpose. What you used to believe is part of how you got here; delete it and the correction looks like it came from nowhere.",
      "insight.superseded.show": { one: "show {n} superseded line", other: "show {n} superseded lines" },
      "insight.superseded.hide": "Fold the superseded ones back up",
      "insight.item.edit": "Edit this line",
      "insight.item.edit.prompt": "Rewrite \"{id}\" — the id stays, the old wording is replaced. If the old wording is still worth reading, supersede it instead of editing it:",
      "insight.supersede.act": "Supersede this line",
      "insight.supersede.prompt": "What replaces \"{id}\"? The old line stays, folded up and marked:",
      "insight.supersede.hint": "Supersede when you found out the old line was wrong. Edit when you only said it badly.",
      "insight.warn.missing": "This supersedes {id}, and there is no {id} in this file — so nothing here says what was replaced.",

      /* ---- 快捷键 ---- */
      "keys.title": "Keyboard",
      "keys.move": "move between steps",
      "keys.toggle": "switch graph / list",
      "keys.new": "new step",
      "keys.edit": "edit",
      "keys.search": "search",
      "keys.back": "back to here",

      /* ---- 编辑器 ---- */
      "editor.save": "Save",
      "editor.cancel": "Cancel",
      "editor.title.placeholder": "Title: one line on what this step is doing",
      "editor.paths.label": "External artifacts · one per line, `location | role | note | k=v`",
      "editor.paths.placeholder": "/blue/group/user/exp/agnews | input | training data | size=12884901888 n=120000 checked=2026-08-09",
      "editor.paths.hint": "`location | note` still means exactly what it always did. A segment that is **exactly** `input` / `script` / `output` / `evidence` becomes the role; a segment whose tokens are **all** `k=v` becomes attributes (`size=` `n=` `md5=` `sha256=` `checked=` `missing=`); everything else is note. So `| the run with lr=3e-4` stays a note — one stray equals sign shouldn't turn a sentence into metadata.",
      "editor.inputs.label": "Data dependencies · one per line, `step id | which artifact`",
      "editor.inputs.placeholder": "013 | pocket_composition.csv\n014 | rmscore_pairs.csv",
      "editor.inputs.hint": "Leave this empty when the data simply came down from the parent. It exists for the times it didn't — the tree can only point at one step, and a pipeline often eats from several.",
      "editor.code.label": "Code · one per line, `kind | location | k=v`",
      "editor.code.placeholder": "snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43",
      "editor.code.hint": "`kind` is `git`, `snapshot` or `container`. An existing `commit:` line keeps working and shows up here as a git entry — it is read from that one line, not copied into a second one.",
      "editor.hint": "Paste a screenshot with **Ctrl+V** and it uploads and inserts itself; anything copied out of Excel or a web table turns into a markdown table; files can be dropped straight into the box. `id` never changes and is never handed out twice — a `[[003b]]` written down anywhere has to keep working forever. `parent` can be moved, but only with a reason, and the move gets written into the note.",
      "editor.status.uploading": "Uploading…",
      "editor.status.inserted": { one: "Inserted {n} file", other: "Inserted {n} files" },
      "editor.status.draftSaved": "Draft saved {time}",
      "editor.draft.found": "Unsaved draft found",
      "editor.draft.moved": "⚠ This draft was written against a different version of the note. It has been changed since — restoring will paint over that change.",
      "editor.draft.restore": "Restore draft",
      "editor.draft.discard": "Discard draft",
      "editor.tool.bold": "Bold (Ctrl+B)",
      "editor.tool.em": "Italic (Ctrl+I)",
      "editor.tool.code": "Inline code",
      "editor.tool.h": "Section heading",
      "editor.tool.ul": "Bulleted list",
      "editor.tool.task": "Task list",
      "editor.tool.quote": "Quote",
      "editor.tool.pre": "Code block",
      "editor.tool.link": "Link",
      "editor.tool.img": "Insert an image (or just paste a screenshot with Ctrl+V)",
      "editor.tool.table": "Insert a table (pasting from Excel also becomes one by itself)",
      "editor.tool.hr": "Horizontal rule",
      "editor.ph.bold": "bold",
      "editor.ph.em": "italic",
      "editor.ph.code": "code",
      "editor.ph.link": "text",

      /* ---- 离开确认 ---- */
      "leave.title": "There are changes you haven't saved",
      "leave.hint": "They are already stored as a draft in this browser, so leaving loses nothing. Next time you open this step you'll be asked whether to bring it back.",
      "leave.stay": "Keep editing",
      "leave.keep": "Leave (keep the draft)",
      "leave.discard": "Discard the draft and leave",

      /* ---- 保存冲突（409） ---- */
      "conflict.title": "This step changed while you were editing it",
      "conflict.why": "The copy on the server has moved on",
      "conflict.server.meta": "On the server now · {author} · {date} · {digest}",
      "conflict.mine.meta": "Yours, still open (already saved as a draft)",
      "conflict.hint": "Highlighted lines are the ones that differ. Nothing is lost either way: keep the server's version and yours stays behind as a draft in this browser.",
      "conflict.cancel": "Back to the editor",
      "conflict.theirs": "Keep the server's version",
      "conflict.mine": "Overwrite with mine",

      /* ---- 新建步骤 ---- */
      "newstep.title": "New step",
      "newstep.draft.found": "Unfinished from last time",
      "newstep.draft.untitled": "(no title yet)",
      "newstep.draft.restore": "Restore",
      "newstep.draft.discard": "Discard",
      "newstep.parent": "Branching from",
      "newstep.parent.none": "(none — this starts a new tree)",
      "newstep.field.title": "Title",
      "newstep.title.placeholder": "One line on what this step is doing",
      "newstep.field.status": "Status",
      "newstep.status.wip": "wip — in progress",
      "newstep.status.done": "done — has a conclusion",
      "newstep.status.dead": "dead — this road ends here",
      "newstep.field.date": "Date",
      "newstep.field.commit": "commit",
      "newstep.commit.placeholder": "can be left empty",
      "newstep.field.code": "Code",
      "newstep.field.inputs": "Data dependencies",
      "newstep.field.body": "Body",
      "newstep.paths.placeholder": "/blue/group/user/exp/agnews | training data, 12 GB\nhttps://github.com/you/repo/tree/9b7d112 | the code this step ran",
      "newstep.hint": "\"Why\" is the one field in this whole system that nothing can generate for you. Logs save themselves, commits record themselves, environments freeze themselves; only \"what made me decide to try this\" has to come from you.",
      "newstep.cancel": "Cancel",
      "newstep.create": "Create",

      /* ---- 跨项目搜索 ---- */
      "search.where.title": "title",
      "search.where.body": "body",
      "search.where.tags": "tags",
      "search.where.id": "id",
      "search.where.paths": "artifact / code location",
      "search.hit.where": "matched in {where}",
      "search.searching": "Searching…",
      "search.failed": "Search failed: {error}",
      "search.none": "Nothing matching \"{q}\" in any of the {projects}.",
      "search.head": "{hits} · {projects}",
      "search.more": " ({total} in all, showing the first {shown})",
      "search.close": "Close",
      "search.static": "A static export is a pile of files you can read with the network off; searching across projects needs the server. Search inside a project page, or reach for `grep -r`.",

      /* ---- 数据仓同步 ---- */
      "git.title": "Data repo sync · {text}",
      "git.state.disabled": "automatic sync is off",
      "git.state.misconfigured": "the data repo isn't set up yet",
      "git.state.idle": "nothing synced yet",
      "git.state.clean": "nothing to sync",
      "git.state.committed": "committed locally, not pushed yet",
      "git.state.pushed": "pushed to the remote",
      "git.state.error": "sync failed",
      "git.hint.disabled": "Set `git.enabled` to true in `config.json` to turn automatic commit and push on.",
      "git.hint.misconfigured": "The data directory has to be a git repo with a remote configured — otherwise the first push fails, silently.",
      "git.hint.idle": "Nothing has been written since the server came up, so there was nothing to sync.",
      "git.hint.error": "Click to retry right now. If it still fails, read the raw git output — it is almost always auth or an unreachable remote.",
      "git.at": "({at})",
      "git.pending": { one: "{n} change still waiting", other: "{n} changes still waiting" },
      "git.fix": "How to fix: {hint}",
      "git.raw": "Raw git output: {detail}",
      "git.warn.error": "⇅ Sync failed",
      "git.warn.misconfigured": "⇅ Sync isn't set up",
      "git.retry": "(click to retry now)",

      /* ---- 外部产物的类型徽章 ---- */
      "path.kind.hpc": "HPC",
      "path.kind.github": "GitHub",
      "path.kind.git": "Git",
      "path.kind.dropbox": "Dropbox",
      "path.kind.drive": "Drive",
      "path.kind.object": "Object store",
      "path.kind.archive": "Archive",
      "path.kind.mlhub": "ML hub",
      "path.kind.url": "Link",
      "path.kind.local": "Local disk",
      "path.kind.path": "Path",

      /* ---- ③ 结构化路径：role / 大小 / 条目数 / 校验和 / 核对状态 ---- */
      "path.role.input": "input",
      "path.role.script": "script",
      "path.role.output": "output",
      "path.role.evidence": "evidence",
      "path.role.input.title": "Went in — this step read it",
      "path.role.script.title": "The code that was run",
      "path.role.output.title": "Came out — this step wrote it",
      "path.role.evidence.title": "Kept as proof — logs, figures, the raw output of the run",
      "path.role.none": "no role given",
      "path.n": { one: "{n} entry", other: "{n} entries" },
      "path.checksum": "{algo} {value}",
      "path.checksum.title": "A {algo} was recorded. If it still matches, these are still the same bytes — not just the same path.",
      "path.checked": "last confirmed present {date}",
      "path.checked.title": "On {date} something looked, and it was there. Nothing has looked since.",
      "path.missing": "gone since {date}",
      "path.missing.badge": "gone",
      "path.missing.title": "It was not there on {date}. The line stays: a location that used to hold 57 GB and now holds nothing is a finding, not a typo to tidy away.",
      "path.unchecked": "never checked",
      "path.unchecked.title": "Nobody has confirmed this location still exists. Writing down where something lives and knowing it is still there are two different facts.",
      "path.summary.missing": { one: "{n} location is gone", other: "{n} locations are gone" },
      "path.attr.unknown.title": "{k} is not an attribute this build knows, so it is kept exactly as written: {v}. The notes are the database — a program that drops what it doesn't recognise is the one that loses your data.",

      /* ---- toast ---- */
      "toast.saved": "Saved",
      "toast.created": "Created {id}",
      "toast.deleted": "Deleted {id}",
      "toast.deleted.orphaned": "Deleted {id}; {ids} are orphans now",
      "toast.deleted.inputs": "Deleted {id}; {ids} still declare `input: {id}` and now point at nothing — go fix those lines",
      "toast.insights.saved": "Saved — only the four insight sections were replaced",
      "toast.insight.added": "Filed under \"{label}\"",
      "toast.insight.updated": "Rewrote {id} — same id, so anything pointing at it still points at it",
      "toast.insight.superseded": "{id} supersedes {old}; {old} is folded up, not gone",
      "toast.moved": "Moved {id} under {parent} — the old parent and your reason are in the note now",
      "toast.moved.root": "{id} is a root of its own now — the move is recorded in the note",
      "toast.copied.path": "Path copied",
      "toast.copy.failed": "Copy failed",
      "toast.draft.restored": "Draft restored",
      "toast.draft.discarded": "Draft discarded",
      "toast.draft.kept": "You left an edit in progress — it's saved as a draft",
      "toast.table.converted": "Turned into a markdown table",
      "toast.token.saved": "Token saved in this browser",
      "toast.token.cleared": "Token cleared",
      "toast.title.required": "A title is required",
      "toast.select.step": "Pick a step first",
      "toast.uploaded": { one: "Uploaded {n} file and wrote it into the body",
                          other: "Uploaded {n} files and wrote them into the body" },
      "toast.conflict.theirs": "Kept the server's version. Yours is still here as a draft — you'll be asked about it next time you open this step.",
      "toast.conflict.mine": "Overwrote with your version",
      "toast.sync.running": "Syncing…",
      "toast.sync.ok": "Sync done: {summary}",
      "toast.sync.failed": "Still failing: {summary}",

      /* ---- prompt / confirm ---- */
      "confirm.file.delete": "Delete the attachment {path}? The step itself stays.",
      "confirm.token": "Write token (leave it empty to clear):",
      "confirm.project.name": "Project name:",
      "confirm.delete.title": "Delete {id} \"{title}\"",
      "confirm.delete.what": "This is a real delete: the directory goes, attachments and all, and the id may be handed out again to the next step.",
      "confirm.delete.children": { one: "⚠ It has {n} child step, which will be orphaned (demoted to a root).",
                                   other: "⚠ It has {n} child steps, which will be orphaned (demoted to roots)." },
      "confirm.delete.consumers": { one: "⚠ {n} step declares `input: {id}` — it reads this step's output. That data-dependency edge goes dangling, and its traceability chain breaks here.",
                                    other: "⚠ {n} steps declare `input: {id}` — they read this step's output. Those data-dependency edges go dangling, and their traceability chains break here." },
      "confirm.delete.refs": { one: "⚠ {n} step writes [[{id}]] in its prose; the link will point at nothing.",
                               other: "⚠ {n} steps write [[{id}]] in their prose; those links will point at nothing." },
      "confirm.delete.reuse": "⚠ Because the id can be handed out again, those dangling edges will silently re-attach to whatever step gets this number next.",
      "confirm.delete.dead": "An experiment that failed should be marked dead, not deleted.",
      "confirm.delete.why": "Why are you deleting it? (Required; it goes into the project's project.md. Once the directory is gone, that one sentence is all that's left — and half a year from now it is the only thing that can tell you why.)",

      /* ---- 计数 · 单位 · 通用 ---- */
      "count.steps": { one: "{n} step", other: "{n} steps" },
      "count.projects": { one: "{n} project", other: "{n} projects" },
      "count.files": { one: "{n} attachment", other: "{n} attachments" },
      "count.images": { one: "{n} figure", other: "{n} figures" },
      "count.children": { one: "{n} child step", other: "{n} child steps" },
      "count.warnings": { one: "{n} warning", other: "{n} warnings" },
      "count.hits": { one: "{n} match", other: "{n} matches" },
      "count.moves": { one: "{n} move", other: "{n} moves" },
      "count.inputs": { one: "{n} input", other: "{n} inputs" },
      "count.consumers": { one: "{n} consumer", other: "{n} consumers" },
      "unit.b": "{n} B",
      "unit.kb": "{n} KB",
      "unit.mb": "{n} MB",
      /* 57 GB 那个目录就是这一轮的起因：只到 MB 的话，它显示成 58366 MB，
         而「58366」这个数没人读得出是多大。 */
      "unit.gb": "{n} GB",
      "unit.tb": "{n} TB",
      "common.untitled": "(untitled)",
      "common.copy": "Copy",
      "common.copied": "Copied",
      "common.close": "Close (Esc)",

      /* ---- 翻译缺失时的如实说明（不是警告，是必要的回退提示） ---- */
      "tr.fallback.note": "Not translated yet — what follows is the original text, in the language it was written in.",
      "tr.fallback.project": "These insights have no translation yet — what follows is the original text.",
      "tr.badge.original": "original",

      /* ---- 内容模板：跟内容语言走，不跟界面语言（见文件头【二】） ---- */
      "template.body": "## Why\n\n\n## What\n\n\n## Result\n\n\n## Conclusion\n\n\n## Next\n",
      "template.table": "| Column 1 | Column 2 | Column 3 |\n|---|---|---|\n|  |  |  |\n|  |  |  |",
    },

    /* ========================================================== 中文 ==== */
    zh: {
      /* ---- 顶栏 ---- */
      "app.proj.switch": "切换项目",
      "app.proj.all": "所有项目 ▸",
      "app.proj.option": "{name}（{n}）",
      "app.search.placeholder": "搜索 id / 标题 / 正文 / 标签   （按 / 聚焦）",
      "app.scope.one": "本项目",
      "app.scope.all": "全部项目",
      "app.scope.one.title": "搜索范围：当前项目（点击搜全部项目）",
      "app.scope.all.title": "搜索范围：全部项目（点击只搜当前项目）",
      "app.view.graph": "图",
      "app.view.list": "列表",
      "app.view.graph.title": "图视图",
      "app.view.list.title": "列表视图",
      "app.view.flow": "数据流",
      "app.view.flow.title": "数据流视图——边是声明出来的输入，不是树",
      "app.new": "＋ 新步骤",
      "app.new.title": "从选中节点派生新步骤（n）",
      "app.newproj": "＋ 项目",
      "app.newproj.title": "新建一个项目",
      "app.token.title": "设置写入令牌",
      "app.token.set": "已设置写入令牌（点击更换或清除）",
      "app.token.unset": "未设置写入令牌 — 只能浏览",
      "app.live": "实时连接",
      "app.live.static": "静态导出 — 只读",
      "app.lang.title": "界面语言",
      "lang.en": "English",
      "lang.zh": "中文",

      /* ---- 项目索引页 ---- */
      "home.new": "＋ 新建项目",
      "home.empty": "还没有项目。点右上角 **＋ 项目** 新建一个。",
      "home.nofilter": "没有名字含「{q}」的项目。\n正文命中在上面的搜索结果里。",
      "home.filter.count": "{shown} / {total} 个项目",
      "home.card.meta": "{steps} · done {done} / wip {wip} / dead {dead}",
      "home.card.latest": "最近 {date}",
      "home.card.warnings": "⚠ {warnings}",

      /* ---- 图 / 列表视图 ---- */
      "list.empty": "这个项目还没有步骤。点右上角 **＋ 新步骤** 开始。",
      "list.legend.done": "done 实线",
      "list.legend.wip": "wip 虚线",
      "list.legend.dead": "dead 点线",
      "list.legend.l0": "链断了",
      "list.legend.reprofail": "复现失败",
      "list.zoom.out": "缩小",
      "list.zoom.in": "放大",
      "list.zoom.reset": "回到 100%",
      "list.zoom.fit": "适应宽度",
      "list.zoom.fit.label": "适应",
      "list.mark.l0": "L0 不可溯源 — {why}",
      "list.mark.reprofail": "复现失败",
      "list.mark.reprofail.note": "复现失败：{note}",

      /* ---- 详情面板 ---- */
      "detail.meta.commit": "commit {commit}",
      "detail.meta.parent": "parent {id}",
      "detail.meta.code": "{kind} {loc}",
      "detail.meta.inputs": "输入 {ids}",
      "detail.act.edit": "✎ 编辑正文",
      "detail.act.delete": "删除",
      "detail.act.delete.title": "真删这一步。只用于误建/测试数据/粘错的敏感信息——失败的实验请标 dead",
      "detail.act.child": "＋ 从这里派生",
      "detail.backlinks": "被这些步骤引用",
      "detail.files": "文件 · {n}",
      "detail.files.empty": "还没有附件。",
      "detail.files.drop": "把日志、脚本、图拖到本页任意位置即可上传，并自动在正文末尾插入引用。想插在正文中间就进编辑模式，直接 Ctrl+V 粘贴截图。",
      "detail.file.remove": "删除",

      /* ---- ① 移动审计：parent 可改，但改动本身要留在文件里 ---- */
      "move.act": "⇄ 改挂到别的父步骤",
      "move.act.title": "树记的是当时的思路，思路有时候归错了地方。移动不动 id，旧的父节点会追加进笔记——不覆盖任何东西。",
      "move.badge": "移动过",
      "move.badge.title": "这一步挂在哪，改过 {n} 次。什么时候改的、从哪挂过来的、为什么改，都在笔记里。",
      "move.head": "移动记录 · {n}",
      "move.entry": "{date} · 从 {from} 移到 {to} —— {reason}（{by}）",
      "move.entry.nobody": "{date} · 从 {from} 移到 {to} —— {reason}",
      "move.from.root": "根",
      "move.to.root": "自己成为一棵树的根",
      "move.by.human": "手动",
      "move.dialog.title": "把 {id} 改挂到别的父步骤",
      "move.dialog.hint": "`id` 不变，也永远不会重发——别人笔记里的 `[[003b]]`、论文脚注里的引用都还有效。变的只是它挂在哪，而这次变动本身也会写进笔记。",
      "move.field.parent": "新的父步骤",
      "move.parent.none": "（无 — 让它自己成为一棵树的根）",
      "move.parent.current": "当前是 {id}",
      "move.field.reason": "为什么移",
      "move.reason.placeholder": "原来挂在那里，错在哪？",
      "move.reason.required": "必填。移动改的是记录的形状，而读者拿到的多半就是那个形状。只有这一句话还能说明它原来不是这样——以及原来那样为什么是错的。",
      "move.submit": "移动",
      "move.cancel": "取消",
      "move.err.reason": "移动必须写原因——「移动」和「弄丢」的差别全在这一句上",
      "move.err.self": "{id} 不能是自己的父步骤。",
      "move.err.cycle": "把 {id} 挂到 {parent} 下面会成环，成了环就没有第一步可以开始读。",
      "move.err.descendant": "{parent} 在 {id} 的下游。一步不能挂到从它自己长出来的东西下面。",
      "move.err.noop": "{id} 本来就挂在 {parent} 下，没有要记的改动。",
      "move.err.project": "只能在同一个项目内移动。每个项目的 id 都从 001 重新开始，跨项目之后每个 `[[…]]` 都会变得有歧义。",
      "move.err.missing": "这个项目里没有 {parent} 这一步。",

      /* ---- ② 数据依赖：树是记录，input 是字节 ---- */
      "input.lead": "**`parent` 是我当时接着哪一步想，`input` 是这些字节从哪来。** 多数时候是同一步；不是的时候，只有这几行说得出来——树上只挂得住一个父节点，而一条流水线可以同时吃四份产物。",
      "input.parent.tip": "树上一步只有一个父节点。数据流是图，所以记成 input，而不是把树掰成图。",
      "input.head": "本步消费的产物 · {n}",
      "input.empty": "没有声明，读起来就是「数据是从父步骤下来的」。不是的时候才需要写一条。",
      "input.entry": "来自 {link} —— {what}",
      "input.entry.bare": "来自 {link}",
      "input.consumers.head": "消费了本步产物的步骤 · {n}",
      "input.consumers.empty": "还没有步骤声明用了本步的产物。",
      "input.warn.missing": "有一条输入指向 {id}，而这里没有这一步。要么被删了，要么打错了——无论哪种，这条依赖都跟不下去了。",
      "input.warn.self": "{id} 把自己声明成了输入。一步不能消费它还没产出的东西。",
      "input.warn.cycle": "数据流成环了：{chain}。总得有一个是最先跑的。",
      "flow.title": "数据流",
      "flow.lead": "这里每一条边都是声明出来的输入。底下的树一点没动——这是对同一批文件的第二种读法，不是第二份拷贝。",
      "flow.legend.tree": "parent 思路的来处",
      "flow.legend.data": "input 字节的来处",
      "flow.legend.both": "两者重合 —— 父步骤同时也是数据的来源",
      "flow.empty": "这个项目还没有声明过输入，数据流就是树本身。",

      /* ---- ⑤ 代码位置：commit 只是其中一种 ---- */
      "code.head": "代码 · {n}",
      "code.kind.git": "git",
      "code.kind.snapshot": "快照",
      "code.kind.container": "容器",
      "code.kind.git.title": "仓库里的一个 commit——历史本身就是证据",
      "code.kind.snapshot.title": "跑的时候那份代码的拷贝，逐文件带校验和",
      "code.kind.container.title": "镜像 digest——代码连同它跑起来所需的一切",
      "code.manifest": "manifest {name}",
      "code.manifest.title": "逐个文件列出名字和校验和的那份清单。有它，一个目录才和 commit 一样管用。",
      "code.files": "{n} 个文件",
      "code.empty": "没记代码位置。",
      "code.from.commit": "来自 commit 那一行",
      "code.from.commit.title": "从 commit 那一行读出来的，没有再存一份。文件里这个事实仍然只有一处。",
      "code.l2.note": "代码在这里，逐文件的校验和也在这里——这不比 commit 差。很多真实的工作就是从一个从没进过 git 的目录里跑起来的，不给它 L2 只会让评级说假话。",

      /* ---- ⑥ 提示级诊断：说清「这条不影响等级」 ---- */
      "lint.head": "提示 · {n}",
      "lint.badge": "提示",
      "lint.note": "只是提示。这些都不影响等级——它们是以后的读者会希望你当时写下来的东西。",
      "lint.level.error": "错误",
      "lint.level.warn": "警告",
      "lint.level.hint": "提示",
      "lint.subheads": "`{section}` 下面没有自己的正文，只有子标题。这算写了——写这一行只是让你知道它是怎么被读的。",
      "lint.table.nodesc": "表格没有一句说明。只拿到一堆数字的人，只能猜哪个方向算好。",
      "lint.pre.nodesc": "代码块没有一句说明——说清它做什么，或者它的输出后来说明了什么。",

      /* ---- 可溯源性 · L0–L4 ---- */
      "trace.title": "可溯源性 · L0–L4",
      "trace.self": "这一步自己",
      "trace.chain": "整条链",
      "trace.weakest": "最弱的一环是 {link} {title}，先补它",
      "trace.weakest.self": "最弱的一环就是这一步",
      "trace.ok": "机械可判的部分都齐了。再往上要有人真去跑一遍。",
      "trace.chip.title": "{level} {name} — {hint}",
      "trace.level.L0": "不可溯源",
      "trace.level.L1": "可读",
      "trace.level.L2": "可定位",
      "trace.level.L3": "可重跑",
      "trace.level.L4": "已复现",
      "trace.level.L0.hint": "「为什么」或「做了什么」空着，或有图没图注，或 done/dead 却没结论",
      "trace.level.L1.hint": "看得懂当时的判断，但跑不了",
      "trace.level.L2.hint": "代码和产物都找得回来——一个 commit，或一个逐文件带校验和的快照目录，或一个镜像 digest",
      "trace.level.L3.hint": "有人确认过命令/环境/种子齐全",
      "trace.level.L4.hint": "有人真跑过，数字在容差内对上了",
      "trace.missing.why": "没写「为什么」——这是唯一无法自动生成的字段",
      "trace.missing.what": "没写「做了什么」——重跑要靠它",
      "trace.missing.conclusion": "没写「结论」——假设到底成不成立",
      "trace.missing.captions": "有图没写图注——图里的信息对文本读者是黑洞",
      "trace.missing.commit": "没记代码位置——commit、带逐文件校验和的快照、镜像 digest，有一个就行；一个都没有，这个结果背后的代码就没了",
      "trace.missing.paths": "没记产物位置——数据和权重在哪不知道",
      "trace.repro.head": "复现记录 · {n}",
      "trace.repro.empty": "还没有人尝试复现。`repro:` 由审计/复现 agent 写回，失败的记录和成功的一样要留着。",
      "trace.repro.verified": "已复现",
      "trace.repro.runnable": "可重跑",
      "trace.repro.failed": "复现失败",
      "trace.repro.unknown": "状态不明",

      /* ---- 项目洞察 ---- */
      "insight.title": "💡 {name} · 洞察",
      "insight.lead": "核心想法 · 什么有效什么无效 · 踩过的坑。存在 `project.md` 里，删掉程序照样能 grep。",
      "insight.empty": "还没有洞察。\n这里记的是**不属于任何单独一步**的判断——「回译在这个数据集上一直没用」是三次尝试之后的结论，挂在哪一步都不对。",
      "insight.edit": "✎ 编辑洞察",
      "insight.add": "＋ {label}",
      "insight.idea": "核心想法",
      "insight.works": "有效",
      "insight.fails": "无效",
      "insight.pitfall": "坑",
      "insight.idea.hint": "还没验证但值得记下来的方向",
      "insight.works.hint": "确认管用的",
      "insight.fails.hint": "确认不管用的——和有效一样重要",
      "insight.pitfall.hint": "会反复咬人的问题",
      "insight.prompt": "记一条「{label}」（一句话说清；带上数字更好）：",
      "insight.editor.title": "💡 编辑项目洞察",
      "insight.editor.hint": "markdown。提交的只有这四个小节（以及标题之前的引言）。`trace_insight` 工具往同样的小节里追加，保持它们在，人和 agent 写的就落在同一处。",
      "insight.editor.others": "下面这些小节不属于洞察，这次编辑不会动它们",
      "insight.editor.others.note": "删除步骤时写下的「为什么删的」就落在 `## 已删除` 里。目录已经没了，这几行是仅存的证据，所以它不进这个编辑框。",
      "insight.editor.warn": "⚠ **{sections}** 不是洞察小节，保存时会被丢弃（磁盘上的那一份保持原样）。",
      /* ---- ④ 洞察的 id / 取代 ---- */
      "insight.id": "编号 {id}",
      "insight.id.title": "这一条的固定把手。以后靠它重锚或取代这一条——句子可以重写，把手不能变。",
      "insight.superseded": "已被 {id} 取代",
      "insight.supersedes": "取代了 {id}",
      "insight.badge.superseded": "已被取代",
      "insight.superseded.title": "它还留着，是故意的。你曾经怎么想，本身就是你怎么走到这一步的一部分；删掉它，那条更正就成了凭空冒出来的。",
      "insight.superseded.show": "展开 {n} 条已被取代的",
      "insight.superseded.hide": "把已被取代的收起来",
      "insight.item.edit": "编辑这一条",
      "insight.item.edit.prompt": "重写「{id}」——id 不变，旧说法被替换掉。旧说法还值得读的话，请用「取代」而不是编辑：",
      "insight.supersede.act": "取代这一条",
      "insight.supersede.prompt": "拿什么取代「{id}」？旧的那条会留着，折起来并标出来：",
      "insight.supersede.hint": "发现旧说法是错的，用「取代」；只是当时话没说清，用「编辑」。",
      "insight.warn.missing": "这一条写了取代 {id}，而这个文件里没有 {id}——于是没有任何地方说得出被取代的是什么。",

      /* ---- 快捷键 ---- */
      "keys.title": "快捷键",
      "keys.move": "在步骤间移动",
      "keys.toggle": "切换图/列表",
      "keys.new": "新建步骤",
      "keys.edit": "编辑",
      "keys.search": "搜索",
      "keys.back": "回到这里",

      /* ---- 编辑器 ---- */
      "editor.save": "保存",
      "editor.cancel": "取消",
      "editor.title.placeholder": "标题：一行说清这一步在干什么",
      "editor.paths.label": "外部路径 · 每行一条，`位置 | 角色 | 说明 | k=v`",
      "editor.paths.placeholder": "/blue/组名/用户名/exp/agnews | input | 训练数据 | size=12884901888 n=120000 checked=2026-08-09",
      "editor.paths.hint": "`位置 | 说明` 的写法和以前一模一样。整段**恰好**是 `input` / `script` / `output` / `evidence` 才算角色；整段的 token **全部**是 `k=v` 才算属性（`size=` `n=` `md5=` `sha256=` `checked=` `missing=`）；其余一律算说明。所以 `| lr=3e-4 的那次运行` 仍然是说明——顺手写个等号不该把一句话变成机器字段。",
      "editor.inputs.label": "数据依赖 · 每行一条，`步骤 id | 消费了哪份产物`",
      "editor.inputs.placeholder": "013 | pocket_composition.csv\n014 | rmscore_pairs.csv",
      "editor.inputs.hint": "数据就是从父步骤下来的话，这里留空。它是给「不是」的那些时候用的——树上只指得到一步，而一条流水线常常同时吃好几份。",
      "editor.code.label": "代码 · 每行一条，`kind | 位置 | k=v`",
      "editor.code.placeholder": "snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43",
      "editor.code.hint": "`kind` 是 `git` / `snapshot` / `container`。已有的 `commit:` 那一行照旧管用，会作为一条 git 显示在这里——它是从那一行读出来的，不是复制成了第二份。",
      "editor.hint": "截图 **Ctrl+V** 直接粘贴会自动上传并插入；从 Excel / 网页表格复制的内容粘贴会自动转成 markdown 表格；文件也可以拖进编辑框。`id` 不可改、也不会重发——任何地方写下的 `[[003b]]` 都必须永远有效。`parent` 可以移动，但必须写原因，而且这次移动会被记进笔记。",
      "editor.status.uploading": "上传中…",
      "editor.status.inserted": "已插入 {n} 个文件",
      "editor.status.draftSaved": "草稿已存 {time}",
      "editor.draft.found": "发现未保存的草稿",
      "editor.draft.moved": "⚠ 这份草稿写的时候，服务器上的正文还是另一版——此后它被改过，恢复会盖掉那次改动",
      "editor.draft.restore": "恢复草稿",
      "editor.draft.discard": "丢弃草稿",
      "editor.tool.bold": "粗体 (Ctrl+B)",
      "editor.tool.em": "斜体 (Ctrl+I)",
      "editor.tool.code": "行内代码",
      "editor.tool.h": "小节标题",
      "editor.tool.ul": "无序列表",
      "editor.tool.task": "任务列表",
      "editor.tool.quote": "引用",
      "editor.tool.pre": "代码块",
      "editor.tool.link": "链接",
      "editor.tool.img": "插入图片（也可以直接 Ctrl+V 粘贴截图）",
      "editor.tool.table": "插入表格（从 Excel 复制的内容直接粘贴也会自动转表格）",
      "editor.tool.hr": "分隔线",
      "editor.ph.bold": "粗体",
      "editor.ph.em": "斜体",
      "editor.ph.code": "code",
      "editor.ph.link": "文字",

      /* ---- 离开确认 ---- */
      "leave.title": "还有没保存的改动",
      "leave.hint": "内容已经自动存进这台机器的浏览器草稿里，离开不会丢。下次打开这一步会问你要不要恢复。",
      "leave.stay": "继续编辑",
      "leave.keep": "离开（保留草稿）",
      "leave.discard": "丢弃草稿并离开",

      /* ---- 保存冲突（409） ---- */
      "conflict.title": "这一步在你编辑期间被改过",
      "conflict.why": "服务器上的内容已经变了",
      "conflict.server.meta": "服务器当前 · {author} · {date} · {digest}",
      "conflict.mine.meta": "你编辑中的（已存成草稿）",
      "conflict.hint": "高亮的是两边不一样的行。选哪个都不会丢东西：保留服务器版本时，你的这份仍然留在浏览器草稿里。",
      "conflict.cancel": "回编辑器再看看",
      "conflict.theirs": "保留服务器版本",
      "conflict.mine": "用我的覆盖",

      /* ---- 新建步骤 ---- */
      "newstep.title": "新建步骤",
      "newstep.draft.found": "上次没写完",
      "newstep.draft.untitled": "(还没写标题)",
      "newstep.draft.restore": "恢复",
      "newstep.draft.discard": "丢弃",
      "newstep.parent": "派生自",
      "newstep.parent.none": "（无 — 新建一棵树的根）",
      "newstep.field.title": "标题",
      "newstep.title.placeholder": "一行说清这一步在干什么",
      "newstep.field.status": "状态",
      "newstep.status.wip": "wip — 在做",
      "newstep.status.done": "done — 有结论",
      "newstep.status.dead": "dead — 此路不通",
      "newstep.field.date": "日期",
      "newstep.field.commit": "commit",
      "newstep.commit.placeholder": "可留空",
      "newstep.field.code": "代码",
      "newstep.field.inputs": "数据依赖",
      "newstep.field.body": "正文",
      "newstep.paths.placeholder": "/blue/组名/用户名/exp/agnews | 训练数据，12 GB\nhttps://github.com/你/仓库/tree/9b7d112 | 跑这一步的代码",
      "newstep.hint": "「为什么」是整个系统里唯一无法自动生成的字段——日志能自动存，commit 能自动记，只有「我当时为什么决定试这个」必须你写。",
      "newstep.cancel": "取消",
      "newstep.create": "创建",

      /* ---- 跨项目搜索 ---- */
      "search.where.title": "标题",
      "search.where.body": "正文",
      "search.where.tags": "标签",
      "search.where.id": "编号",
      "search.where.paths": "产物／代码位置",
      "search.hit.where": "命中：{where}",
      "search.searching": "搜索中…",
      "search.failed": "搜索失败：{error}",
      "search.none": "全部 {projects} 里都没有「{q}」。",
      "search.head": "{hits} · {projects}",
      "search.more": "（共 {total} 条，只列前 {shown} 条）",
      "search.close": "关闭",
      "search.static": "静态导出是断网可读的一堆文件，跨项目搜索需要服务端。请在项目页内搜索，或用 `grep -r`。",

      /* ---- 数据仓同步 ---- */
      "git.title": "数据仓同步 · {text}",
      "git.state.disabled": "未启用自动同步",
      "git.state.misconfigured": "数据仓还没配好",
      "git.state.idle": "还没有同步过",
      "git.state.clean": "没有要同步的改动",
      "git.state.committed": "已提交，还没推到远端",
      "git.state.pushed": "已推送到远端",
      "git.state.error": "同步失败",
      "git.hint.disabled": "要自动提交和推送，把 `config.json` 里的 `git.enabled` 设成 true。",
      "git.hint.misconfigured": "数据仓得先是一个配好远端的 git 仓库，否则第一次 push 就会失败，而且是无声的。",
      "git.hint.idle": "服务起来之后还没有写入，所以没有东西要同步。",
      "git.hint.error": "点一下立刻重试。还是失败就看 git 原文，多半是认证或者远端不通。",
      "git.at": "（{at}）",
      "git.pending": "还有 {n} 处改动在等",
      "git.fix": "怎么修：{hint}",
      "git.raw": "git 原文：{detail}",
      "git.warn.error": "⇅ 同步失败",
      "git.warn.misconfigured": "⇅ 同步没配好",
      "git.retry": "（点击立即重试）",

      /* ---- 外部产物的类型徽章 ---- */
      "path.kind.hpc": "超算",
      "path.kind.github": "GitHub",
      "path.kind.git": "Git",
      "path.kind.dropbox": "Dropbox",
      "path.kind.drive": "Drive",
      "path.kind.object": "对象存储",
      "path.kind.archive": "数据仓库",
      "path.kind.mlhub": "实验平台",
      "path.kind.url": "链接",
      "path.kind.local": "本机",
      "path.kind.path": "路径",

      /* ---- ③ 结构化路径：role / 大小 / 条目数 / 校验和 / 核对状态 ---- */
      "path.role.input": "输入",
      "path.role.script": "脚本",
      "path.role.output": "产物",
      "path.role.evidence": "证据",
      "path.role.input.title": "喂进来的：这一步读了它",
      "path.role.script.title": "跑起来的那份代码",
      "path.role.output.title": "跑出来的：这一步写的",
      "path.role.evidence.title": "留作证据：日志、图、跑完的原始输出",
      "path.role.none": "没标角色",
      "path.n": "{n} 条",
      "path.checksum": "{algo} {value}",
      "path.checksum.title": "记了 {algo} 校验和。还对得上，说明是同一批字节，而不只是同一个路径。",
      "path.checked": "最后确认存在 {date}",
      "path.checked.title": "{date} 有人（或程序）看过，那时它在。此后没有再看过。",
      "path.missing": "{date} 起不存在",
      "path.missing.badge": "已不存在",
      "path.missing.title": "{date} 去看，它不在了。这一行不删：一个曾经装着 57 GB、现在空了的位置是一条发现，不是要顺手清掉的笔误。",
      "path.unchecked": "从未核对过",
      "path.unchecked.title": "没有人确认过这个位置还在。记下它在哪，和知道它还在，是两个不同的事实。",
      "path.summary.missing": "{n} 处位置已不存在",
      "path.attr.unknown.title": "{k} 不是这一版认得的属性，所以原样保留：{v}。笔记就是数据库——一个把自己不认识的东西丢掉的程序，正是最后弄丢你数据的那个。",

      /* ---- toast ---- */
      "toast.saved": "已保存",
      "toast.created": "已创建 {id}",
      "toast.deleted": "已删除 {id}",
      "toast.deleted.orphaned": "已删除 {id}；{ids} 已变成孤儿",
      "toast.deleted.inputs": "已删除 {id}；{ids} 还声明着 `input: {id}`，现在指空了——请去改掉那几行",
      "toast.insights.saved": "已保存（只替换了四个洞察小节）",
      "toast.insight.added": "已记入「{label}」",
      "toast.insight.updated": "已重写 {id}——id 没变，指向它的引用照样有效",
      "toast.insight.superseded": "{id} 取代了 {old}；{old} 折起来了，没有删",
      "toast.moved": "已把 {id} 挂到 {parent} 下——原来的父节点和你写的原因都进笔记了",
      "toast.moved.root": "{id} 现在自己是一棵树的根了——这次移动已记进笔记",
      "toast.copied.path": "已复制路径",
      "toast.copy.failed": "复制失败",
      "toast.draft.restored": "已恢复草稿",
      "toast.draft.discarded": "草稿已丢弃",
      "toast.draft.kept": "离开了编辑中的内容，已存成草稿",
      "toast.table.converted": "已转成 markdown 表格",
      "toast.token.saved": "令牌已保存到本浏览器",
      "toast.token.cleared": "令牌已清除",
      "toast.title.required": "标题不能为空",
      "toast.select.step": "先选一个步骤",
      "toast.uploaded": "已上传 {n} 个文件并写入正文",
      "toast.conflict.theirs": "已保留服务器版本；你的改动留在草稿里，下次打开这一步会问你要不要恢复",
      "toast.conflict.mine": "已用你的版本覆盖",
      "toast.sync.running": "正在同步…",
      "toast.sync.ok": "同步完成：{summary}",
      "toast.sync.failed": "仍然失败：{summary}",

      /* ---- prompt / confirm ---- */
      "confirm.file.delete": "删除附件 {path}？（步骤本身不会被删除）",
      "confirm.token": "写入令牌（留空则清除）：",
      "confirm.project.name": "项目名：",
      "confirm.delete.title": "删除 {id}「{title}」",
      "confirm.delete.what": "这是真删：目录连同附件一起移除，id 可能被下一步重用。",
      "confirm.delete.children": "⚠ 它有 {n} 个子步骤，会变成孤儿（降级为根）。",
      "confirm.delete.consumers": "⚠ 有 {n} 步声明了 `input: {id}`——它们读的是这一步的产物。这条数据依赖会指空，它们的可溯源链断在这里。",
      "confirm.delete.refs": "⚠ 有 {n} 步的正文里写着 [[{id}]]，那些链接会指不到东西。",
      "confirm.delete.reuse": "⚠ 而 id 可能被重用——到那时这些指空的边会**无声地**改指到下一个拿到这个号的步骤上。",
      "confirm.delete.dead": "失败的实验请改标 dead，不要删。",
      "confirm.delete.why": "为什么删？（必填，会记进项目的 project.md。目录一删，这一句话就是仅存的东西——半年后它是唯一能告诉你原因的那一行。）",

      /* ---- 计数 · 单位 · 通用 ---- */
      "count.steps": "{n} 步",
      "count.projects": "{n} 个项目",
      "count.files": "{n} 个附件",
      "count.images": "{n} 张图",
      "count.children": "{n} 个子步骤",
      "count.warnings": "{n} 条警告",
      "count.hits": "{n} 条命中",
      "count.moves": "{n} 次移动",
      "count.inputs": "{n} 条输入",
      "count.consumers": "{n} 个下游",
      "unit.b": "{n} B",
      "unit.kb": "{n} KB",
      "unit.mb": "{n} MB",
      /* 57 GB 那个目录就是这一轮的起因：只到 MB 的话，它显示成 58366 MB，
         而「58366」这个数没人读得出是多大。 */
      "unit.gb": "{n} GB",
      "unit.tb": "{n} TB",
      "common.untitled": "(无标题)",
      "common.copy": "复制",
      "common.copied": "已复制",
      "common.close": "关闭（Esc）",

      /* ---- 翻译缺失时的如实说明（不是警告，是必要的回退提示） ---- */
      "tr.fallback.note": "这一步还没有译文，下面是原文，语言就是它写下时的那一种。",
      "tr.fallback.project": "这些洞察还没有译文，下面是原文。",
      "tr.badge.original": "原文",

      /* ---- 内容模板：跟内容语言走，不跟界面语言（见文件头【二】） ---- */
      "template.body": "## 为什么\n\n\n## 做了什么\n\n\n## 结果\n\n\n## 结论\n\n\n## 下一步\n",
      "template.table": "| 列 1 | 列 2 | 列 3 |\n|---|---|---|\n|  |  |  |\n|  |  |  |",
    },
  };

  /* 服务端算出来的 missing 清单是中文的（trace_core.traceability()，Python 侧不在
     翻译范围里），可它偏偏要显示在可溯源性面板上。所以这里按一段**稳定的判别子串**
     把它认回 key。认不出来就原样显示：老老实实给中文，好过把这一条悄悄吞掉——
     那正是 L0–L4 最该说清楚的部分。
     判别子串取的是小节名和「commit / 图注 / 产物位置」这些不会随文案润色而变的词。 */
  var MISSING_MATCH = [
    ["「为什么」", "trace.missing.why"],
    ["「做了什么」", "trace.missing.what"],
    ["「结论」", "trace.missing.conclusion"],
    ["图注", "trace.missing.captions"],
    /* ⑤ 之后「代码位置」不再只有 commit 一种写法（快照目录 + 逐文件校验和同样够 L2），
       服务端那一条的措辞因此可能是「没记 commit」也可能是「没记代码位置」。两条判别
       子串指向**同一个** key：这不是两件事，是同一件事的两种说法，界面上只该有一句话。
       「产物位置」放在后面且判别串更长，不会被「代码位置」抢走。 */
    ["代码位置", "trace.missing.commit"],
    ["commit", "trace.missing.commit"],
    ["产物位置", "trace.missing.paths"],
  ];

  /* ------------------------------------------------------------------ 内部 */

  // window 在浏览器里，globalThis 在 node 里（测试要能改掉 localStorage / 事件派发，
  // 所以每次现取，不在载入时钉死一个引用）。
  function root() { return typeof window !== "undefined" ? window : global; }

  // localStorage 在隐私模式 / node 下可能不存在或抛异常。语言偏好丢了最多是回到
  // 默认英文，不该让整个界面起不来。
  var memory = {};
  function readStore(k) {
    try {
      var ls = root().localStorage;
      var v = ls ? ls.getItem(k) : null;
      return v === null || v === undefined ? (k in memory ? memory[k] : null) : v;
    } catch (e) { return k in memory ? memory[k] : null; }
  }
  function writeStore(k, v) {
    memory[k] = v;
    try { if (root().localStorage) root().localStorage.setItem(k, v); } catch (e) { /* 忽略 */ }
  }

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  /* 和 md.js 的 esc 逐字符一致，但**故意各写一份**：i18n.js 要能在 md.js 之前载入，
     也要能被 node 单独 require（tests/i18n.test.js）。为了省五行而制造载入顺序
     依赖，代价是白屏。 */
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  var warned = {};
  function warnOnce(msg) {
    if (warned[msg]) return;
    warned[msg] = 1;
    if (typeof console !== "undefined" && console.warn) console.warn("[i18n] " + msg);
  }

  function normalize(l) {
    l = String(l === null || l === undefined ? "" : l).trim();
    return STRINGS[l] ? l : "";
  }

  function lang() { return normalize(readStore(STORE_KEY)) || DEFAULT; }

  function setLang(l) {
    var next = normalize(l);
    if (!next) { warnOnce("setLang(" + JSON.stringify(l) + ")：没有这个语言，忽略"); return lang(); }
    writeStore(STORE_KEY, next);
    var g = root();
    if (g && typeof g.dispatchEvent === "function") {
      var ev;
      // CustomEvent 在浏览器和 node 18+ 都有；没有就退回一个最朴素的对象，
      // 监听方读的是 detail.lang，两条路一样。
      if (typeof CustomEvent === "function") ev = new CustomEvent(EVENT, { detail: { lang: next } });
      else ev = { type: EVENT, detail: { lang: next } };
      g.dispatchEvent(ev);
    }
    return next;
  }

  /* 取原始文案。找不到就依次退：当前语言 → DEFAULT → key 本身。每一级都 warn 一次。 */
  function lookup(l, key) {
    var table = STRINGS[l] || STRINGS[DEFAULT];
    if (table && key in table) return table[key];
    var fb = STRINGS[DEFAULT];
    if (fb && key in fb) {
      warnOnce(l + " 缺 key：" + key + "（暂时用 " + DEFAULT + " 顶上）");
      return fb[key];
    }
    warnOnce("没有这条文案：" + key);
    return null;
  }

  /* 复数。值写成 {one, other} 时按 vars.n 选；中文这一侧一律是普通字符串。 */
  function plural(v, vars) {
    if (v === null || typeof v !== "object") return v;
    var n = vars && vars.n;
    return (Number(n) === 1 && "one" in v) ? v.one : v.other;
  }

  /* {name} 占位替换。缺变量时把占位符原样留着并 warn——留一个显眼的 {n} 在界面上，
     总比悄悄变成空白让人以为文案就长这样要好。 */
  function fill(text, vars, transform) {
    return String(text).replace(/\{(\w+)\}/g, function (whole, name) {
      if (!vars || !(name in vars)) {
        warnOnce("插值缺变量 {" + name + "}：" + text);
        return whole;
      }
      return transform(vars[name]);
    });
  }

  function identity(v) { return v === null || v === undefined ? "" : String(v); }

  function raw(l, key, vars) {
    var v = lookup(l, key);
    if (v === null) return key;
    return plural(v, vars);
  }

  /* 纯文本。变量不转义——目的地是 textContent / title= / prompt()，那些地方
     转义是错的（会显示成 &quot;）。 */
  function tIn(l, key, vars) {
    return fill(raw(normalize(l) || DEFAULT, key, vars), vars, identity);
  }
  function t(key, vars) { return tIn(lang(), key, vars); }

  /* HTML。顺序是「先转义整条文案 → 再展开行内标记 → 最后插变量」：
     1) 先转义：表里就算哪天混进 <script> 也只会显示成字，不会执行；
     2) 后展开标记：只认 **粗体** / `代码` / 换行三种，全都是我们自己造出来的标签；
     3) 变量最后插：这样变量里的 ** 和反引号不会变成标记（标题里出现 ** 很常见），
        而 **{x}** 依然有效——<b> 是在占位符还在的时候就套好的。
     要放已经拼好的 HTML（比如一个 <a>）就写 {link: {html: "<a …>"}}，显式声明。 */
  function tHtmlIn(l, key, vars) {
    var text = esc(raw(normalize(l) || DEFAULT, key, vars));
    text = text
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
    return fill(text, vars, function (v) {
      if (v && typeof v === "object" && typeof v.html === "string") return v.html;
      return esc(v);
    });
  }
  function tHtml(key, vars) { return tHtmlIn(lang(), key, vars); }

  function has(key) {
    var table = STRINGS[lang()];
    return !!(table && key in table);
  }
  function keys(l) {
    var table = STRINGS[normalize(l) || lang()] || {};
    return Object.keys(table);
  }

  /* 日期和数字交给 toLocaleString 时用哪个 locale。界面是英文却把日期排成
     「2026/8/9」是最容易被忽略的那种半吊子翻译。 */
  var LOCALE = { en: "en-US", zh: "zh-CN" };
  function locale() { var l = lang(); return LOCALE[l] || l; }

  /* 服务端给的中文 missing 条目 → 本语言的说法。认不出就原样返回。 */
  function traceMissing(text) {
    var s = String(text === null || text === undefined ? "" : text);
    for (var i = 0; i < MISSING_MATCH.length; i++) {
      if (s.indexOf(MISSING_MATCH[i][0]) >= 0) return t(MISSING_MATCH[i][1]);
    }
    return s;
  }

  var i18n = {
    DEFAULT: DEFAULT,
    STORE_KEY: STORE_KEY,
    EVENT: EVENT,
    STRINGS: STRINGS,
    lang: lang,
    setLang: setLang,
    t: t,
    tHtml: tHtml,
    /* 显式指定语言的两个变体。正文模板必须走它：模板是要写进 note.md 的内容，
       跟的是**内容语言**，不是界面语言（见文件头【二】）。 */
    tIn: tIn,
    tHtmlIn: tHtmlIn,
    esc: esc,
    has: has,
    keys: keys,
    locale: locale,
    traceMissing: traceMissing,
  };

  global.i18n = i18n;
  if (typeof window !== "undefined") window.i18n = i18n;
  if (typeof module !== "undefined" && module.exports) module.exports = i18n;
})(typeof globalThis !== "undefined" ? globalThis : this);

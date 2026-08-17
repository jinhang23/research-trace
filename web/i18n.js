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
      /* ⑧ 两条路径。它们在切换器里并排站着，所以这两个名字必须互相解释：一个回答
         「我是怎么走到这儿的」，一个回答「该怎么做」。第一次看见两个视图的人，
         能不能分清自己在看什么，全靠这两行和下面 pipeline.*.lead 那两句。 */
      "app.view.dev": "Development path",
      "app.view.dev.title": "Everything that was recorded, dead ends and undecided forks included. The view for yourself: it answers how you ended up here.",
      "app.view.pipeline": "Final pipeline",
      "app.view.pipeline.title": "Only the chain that produced the results, traced back through the declared inputs. The view for everyone else: it answers what to do.",
      /* ⑨ 章节的筛选器。只在项目里真有章节时才出现——一个 `chapter:` 都没写的
         项目必须完全无感，多一个恒灰的下拉框就已经不是无感了。 */
      "app.chapter.all": "All chapters",
      "app.chapter.title": "Narrow the view to one part of the project. The chapters are read off the steps themselves — there is no list of them kept anywhere.",
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
      /* ⑦ 三种关系的图例。摆在 status 那三条后面是有意的：读者先分清「线型说的是
         状态」，再看「颜色说的是关系」，两套通道就不会在脑子里打架。
         每条写成「名字 —— 一句话」，因为第一次看见彩色边的人全靠这里知道它是什么
         意思；名字刻意避开「分支 / 主线」，那两个词在 git 里已经有别的含义了。 */
      "list.legend.extends": "continues — one step further down the same road",
      "list.legend.alternative": "alternative — one of several answers to the same question, and only one road gets taken",
      "list.legend.rejoin": "rejoins — something made on this side road was read by a later step on another one",
      "list.legend.extends.title": "The ordinary case, and the one you should never have to think about: this step picks up where its parent left off.",
      "list.legend.alternative.title": "A question was asked at the parent and these are the answers on offer. The set is read off the tree — every child that calls itself a candidate is in it. Siblings never register each other, so there is no way for two of them to disagree about who is whose alternative.",
      "list.legend.rejoin.title": "Not a tree edge at all. It is a declared input, drawn here so that a side road which came back does not read as a dead end. The curve and the arrowhead say so by themselves: a tree edge is never curved.",
      "list.legend.note": "Colour is for scanning, shape is for reading. A set of alternatives is bracketed and a rejoin is always a curve with an arrowhead — so a greyscale printout, or a reader who sees no colour, loses the glance and not the meaning.",
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

      /* ---- ①b 拖着改父节点 ----
         手势省掉的是「在下拉框里翻 id」的那十几秒，不是那句原因。所以这一组文案
         全都短——它们贴着指针走，读它们的人正在拖东西，一眼一句。
         唯一长的一句是 move.dragged.note：它要挡住手势最容易造成的误会，
         也就是「我刚才把数据流也接过去了」。 */
      "move.dragged.note": "Dragging picked the new parent, and nothing else. `parent` is which step the thinking continued from. `inputs` — where the bytes came from — is untouched, and so is every word of the note.",
      "drag.aim.parent": "→ hangs under {parent}",
      "drag.aim.root": "→ becomes a root of its own",
      "drag.aim.none": "Drop it on a step to hang it there",
      "drag.no.self": "A step can't be its own parent",
      "drag.no.descendant": "{parent} grew out of {id}",
      "drag.no.noop": "Already hangs right here",
      "drag.no.missing": "No such step",
      "drag.carry": { one: "{n} step below comes along", other: "{n} steps below come along" },
      "drag.root.label": "Make it a root of its own",
      "drag.root.hint": "Only here. Dropping on blank space anywhere else calls the move off.",
      "drag.cancelled": "Move called off — nothing was written.",
      "drag.readonly": "This page is a static export: a photograph of the record, not the record. Moving a step appends a line to its note, and only the server can write that.",
      "drag.flow": "The edges here are inputs — where the bytes came from. Dragging changes parent — which step the thinking continued from. Move a step in the tree or the list; the two are kept apart on purpose.",

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
      /* core 发的这四条以前没有对应文案，于是在英文界面上原样漏出整句中文——
         而它们恰恰是最该被读懂的：三条「记录里少了半年后补不回来的那部分」，
         一条「这张图对文本读者是黑洞」。四条都不带变量，`where` 已经指明了文件。 */
      "lint.missing.why": "Marked done or dead with no **Why**. This is the one field nothing can generate for you: logs save themselves, commits record themselves, but why you decided to try this is gone the moment you forget it.",
      "lint.missing.what": "Marked done or dead with no **What**. Re-running depends on it — with only a title, the next reader (including you in six months) has nowhere to start.",
      "lint.missing.conclusion": "Marked done or dead with no **Conclusion** — did the hypothesis hold or not. A step that ended without saying how it ended is the shape a dead end takes when nobody wrote it down.",
      "lint.figure.nocaption": "An image with no caption. Write it as `![](… \"what this figure shows\")`. You and every agent reading this record see only that one line — without a caption, whatever the picture showed is lost to the text.",
      "lint.subheads": "`{section}` has no prose of its own, only sub-headings beneath it. That counts as written — this line is here so you know how it was read.",
      "lint.table.nodesc": "A table with nothing said about it. Someone handed only the numbers has to guess which direction is better.",
      "lint.pre.nodesc": "A code block with nothing said about it — say what it does, or what its output turned out to mean.",
      /* ⑦ 的三条诊断。全是提示级：一个岔路口没做决定不是缺陷，是研究的正常状态，
         所以每一条都要自己说出「这不影响等级」——lint.note 那句总说明会被人当背景板
         略过，而这三条恰恰最容易被误读成「我这里做错了」。 */
      "lint.alt.lone": "Marked as a candidate with nothing beside it — a choice between one road. Either the road not taken never got written down, or this is a plain next step and the mark can come off. Neither reading changes the level.",
      "lint.fork.noquestion": "{n} roads leave this step and no line says what the choice is between. That there were two roads is visible from the tree; what the fork was about is exactly the part the tree cannot hold. A hint only — the level is untouched.",
      "lint.fork.open": "{n} candidates here are still live, so this fork has no answer yet. Nothing is wrong: running several roads at once is how the work actually goes. It is listed because an open fork is a question you are still carrying, and because marking the roads you gave up on as dead is far easier now than a year from now. The level is untouched.",
      /* fork_without_decision 的镜像。它比那一条更容易被当成 bug：写下的问题在
         页面上看得见，可它不成组、不进 forks 清单、图上也没有括弧——人最先想到的
         解释是「没保存上」。所以这句话要先说「记下了」，再说「还差什么」。 */
      "lint.fork.nocandidates": "{id} says what the choice is between, but no step below it has declared itself a candidate. The line is saved; it just isn't a fork yet — nothing is grouped, no bracket is drawn, and it won't show up among the open decisions. Mark each road with `alternative` and it stands up. The level is untouched either way.",
      /* branch: 那一行拼错时的降级提醒。没有这条 key，英文界面上会原样漏出
         服务端那句中文——而这一条恰恰是给刚打错字的人看的。 */
      "lint.branch.unknown": "`branch: {branch}` isn't a value this reads. The step is being treated as a plain continuation, which means the mark you wrote is having no effect at all. The two values are `extends` and `alternative`.",

      /* ---- ⑦ 决策分叉与汇回：树上的边不再只有一种含义 ----
         存储上只多了两个键（候选自己写 `branch: alternative`，分叉点自己写
         `decision:`），"这一组是互斥的""选了哪一个"全是扫出来的派生结论。
         文案要把这一点说出来，否则人会去找一个"标记赢家"的按钮，找不到就以为
         功能缺了一半——而那个按钮正是双真相源本身。 */
      "branch.kind.extends": "continuation",
      "branch.kind.alternative": "alternative",
      "branch.kind.extends.title": "Carries on from the parent. This is what an edge means when nothing says otherwise, which is why it is never written into the note.",
      "branch.kind.alternative.title": "One of the roads out of a question, and the roads are exclusive — taking this one means giving the others up. Each candidate says that about itself; the set is what you get by reading them together.",
      "branch.badge": "candidate",
      "branch.badge.title": "This step is one of several answers to the same question. The others sit right beside it, under the same parent.",

      "decision.pick": "1 of {n}",
      "decision.pick.title": "{n} roads leave this question and one of them gets taken. Which one is not stored anywhere — the moment the others are marked dead, the survivor is the answer.",
      "decision.settled": "settled · {id}",
      "decision.settled.title": "Every other candidate here is marked dead, so {id} is the road that was taken. Nobody wrote that down; it is what the dead ones add up to.",
      "decision.alldead": "all abandoned",
      "decision.alldead.title": "Every road out of this question ended in dead. The question still stands, and that it has no answer is a finding in its own right — not a gap to be tidied away.",
      // 一个候选不成其为选择。这块牌子以前跟着 state 说「已定」——而 state 只数
      // 还活着的候选，1 个活的就叫已定，对 2 选 1 是对的，对「从头到尾只有 1 条」
      // 是在替人宣布一件没发生的事。CLI、MCP 和顶上的提示栏说的都是这一句。
      "decision.lone": "only one road",
      "decision.lone.title": "{id} is marked as a candidate with nothing beside it. A choice between one road was never a choice — either the other candidate is missing its own branch: alternative, or this one is just an ordinary continuation.",
      "decision.open": "not decided yet",
      "decision.open.title": "More than one candidate here is still alive. That says where the work stands, not that anything is wrong with it.",
      "decision.open.note": "{n} of these roads are still live, so nothing has been decided here yet — a perfectly normal place to be. It reads as settled the moment the roads you gave up on are marked dead, which is worth doing while you still remember why you gave them up.",
      "decision.open.summary": { one: "{n} decision is still open", other: "{n} decisions are still open" },
      "decision.open.summary.title": "Forks where more than one candidate is still alive. This is the list to read first when you come back to a project after a month away — an open fork is a question you are still carrying around.",
      "decision.question.label": "What is being decided",
      "decision.question.title": "Written by hand, on the step the roads leave from. Nothing can work it out for you: two children prove there were two roads, never what the choice was between.",
      "decision.question.missing": "Nothing here says what the choice is between. The roads are visible; the question they answer is not.",
      /* 「问题写了、候选还没标」。这一步的 fork 是 null，所以详情面板里只有孤零零
         一行问题——不说清它现在还不成其为岔路口，人只会以为界面漏掉了候选。 */
      "decision.question.orphan": "Saved — but no step below has said `branch: alternative` about itself yet, so this isn't a fork yet: nothing is grouped, no bracket is drawn on the tree, and it stays out of the open-decisions list. Mark each road as a candidate and the question stands up around them.",
      "decision.head": { one: "Decision point · {n} candidate", other: "Decision point · {n} candidates" },
      "decision.lead": "The roads below are answers to one question, and only one of them carries on. Which one won is not a field anywhere — it is read off the others being marked `dead`, so there is no second copy of it to go stale.",
      "decision.candidate.entry": "{link} — {what}",
      "decision.candidate.entry.bare": "{link}",
      "decision.candidate.dead": "ruled out",
      "decision.candidate.live": "still live",
      "decision.of.head": "A candidate at the fork on {parent}",
      "decision.of.lead": "This step is one answer to the question asked at {parent}. Only one of the roads there carries on; the rest end up marked `dead`, and that is the whole record of which one won.",
      "decision.siblings.head": { one: "The other candidate · {n}", other: "The other candidates · {n}" },
      "decision.siblings.lone": "Nothing else under this parent carries the mark, so the set holds one road. Either the road not taken was never written down, or this was simply the next step and the mark can come off.",
      /* 根之间也能成一组（两种互斥的开局）。它没有分叉点，于是没有任何一步能承载
         那句 `decision:`——界面得如实说出来，不然人会到处找那个写问题的地方。 */
      "decision.roots.head": { one: "{n} way to start", other: "{n} ways to start, and one gets taken" },
      "decision.roots.note": "These candidates are roots: there is no step above them to hang the question on, so what is being decided has nowhere to live here. Put it in the title of each road instead.",
      "decision.mark.act": "Mark as a candidate",
      "decision.mark.act.title": "Say that this step and its siblings answer the same question, and that only one of them will be carried on with.",
      "decision.unmark.act": "Not a candidate after all",
      "decision.unmark.act.title": "Back to an ordinary continuation. The id, the note and every sibling stay exactly as they are — what changes is how the edge above this step reads.",
      "decision.write.act": "✎ Write what is being decided",
      "decision.write.act.title": "One line on the question these roads answer. It hangs on this step, the one they all leave from.",
      "decision.write.prompt": "What is being decided here? One line, phrased as the question the roads below are answers to:",

      "rejoin.kind": "rejoin",
      "rejoin.kind.title": "An input that crosses from one road to another: work done off to the side was read by a later step over here. It is the same line the data-flow view draws — the tree simply stopped hiding it.",
      "rejoin.lead": "A side road doesn't always end where it stops. When something it produced is read later by another line, that is a rejoin — and it is an `input:`, not a second kind of parent. No new field was invented for it; the edge has been in the file all along.",
      "rejoin.out.head": { one: "This step's output rejoined {n} step", other: "This step's output rejoined {n} steps" },
      "rejoin.in.head": { one: "Rejoined here from {n} other road", other: "Rejoined here from {n} other roads" },
      "rejoin.entry": "{link} — {what}",
      "rejoin.entry.bare": "{link}",
      /* 汇回边自带「两条路是在哪儿分开的」（core 算的 LCA）。说出这个岔路口，
         「它绕回来了」才有具体的形状——否则读者只知道有一条曲线。 */
      "rejoin.at": "the two roads parted at {id}",
      "rejoin.badge": "rejoins",
      "rejoin.badge.title": "Something produced on another road was read here, or something produced here was read over there. The curved edges on the graph are these.",

      /* ---- ⑧ 两条路径：记录与方法的分家 ----
         磁盘上只多了一行 `result:`（写在 project.md 的 front-matter 上，可重复）。
         「哪些步在流程里」「什么顺序」「整条链多可靠」全是从成果沿 `input:` 反向
         闭包算出来的，一个字都不存——存了就是一份会漂移的中心索引（P1 禁止）。
         文案必须把这件事说出来，否则人会去找一个「流程成员清单」的编辑框，
         找不到就以为功能缺了一半——而那个清单正是双真相源本身。 */
      "pipeline.name": "Final pipeline",
      "pipeline.lead": "The chain that actually produced the results, and nothing else. It is what someone else follows to get the same numbers, and what a Methods section is made of.",
      "pipeline.dev.name": "Development path",
      "pipeline.dev.lead": "Every step there is, including the roads that went nowhere and the forks you are still carrying. It is what you read when something looks wrong and you need to know how it got that way.",
      /* 这一句是两条路径全部的关键。不写它，人会把定稿流程理解成「开发路径的
         精简版」，然后开始问「为什么这一步被藏起来了」——而它们回答的根本是
         两个不同的问题。 */
      "pipeline.pair.note": "Neither view is a tidier version of the other. The development path is **how you got there** — what was tried, what died, the moment a choice got made. The final pipeline is **what to do** — the shortest honest account of how the result came about. A step can matter enormously to the first and belong nowhere in the second.",
      "pipeline.head": { one: "Final pipeline · {n} step", other: "Final pipeline · {n} steps" },
      "pipeline.order.note": "In the order the work has to happen: a step comes after everything it reads. Where two steps depend on nothing of each other's, the id decides — so the same record always compiles to the same order, and two exports can be diffed.",
      "pipeline.derived.note": "No list of members is stored anywhere. From each result the chain is followed backwards along `input:`; a step that declares none falls back to its `parent`, which is the step it in fact came after. Anything marked `dead` drops out. Keep a stored list instead and it starts lying the first time you move a step.",

      /* 一个 `result:` 都没声明是绝大多数项目的常态，所以这四句是这个功能的门面。
         它要说清「定稿流程是什么」「为什么值得声明」「怎么声明」——不是报错，
         也不是空荡荡一句「暂无数据」。 */
      "pipeline.empty.title": "Nothing has been declared a result yet",
      "pipeline.empty.what": "A final pipeline is the chain of steps that actually produced a result — traced backwards from that result through what each step declared as its `input:`, with the dead ends dropped. It is not a second copy of anything: every member is worked out on the spot, so it follows along as you move a step, add an input, or mark a road dead.",
      "pipeline.empty.why": "Declaring one is worth the thirty seconds, because it is the only part of this nothing can work out for you — which of two hundred steps is the one you would put in a paper. Once it is declared, the pipeline, its weakest step and a Methods draft all come for free.",
      "pipeline.empty.how": "One line in the project's front matter: `result: 023 | affinity prediction, AUC 0.91`. Several are fine — one per figure, one per claim. Or open a step and hit **{act}**.",
      "pipeline.empty.act": "Mark a step as a result",

      "pipeline.result.head": { one: "Declared result · {n}", other: "Declared results · {n}" },
      "pipeline.result.lead": "This is the one thing about the pipeline that is written down rather than read off the notes. Which steps are in it, in what order, and how far it can be followed are all worked out again every time.",
      "pipeline.result.entry": "{link} — {what}",
      "pipeline.result.entry.bare": "{link}",
      "pipeline.result.badge": "result",
      "pipeline.result.badge.title": "A declared result: one of the things this project set out to produce. Whatever it took to get here is the pipeline.",
      "pipeline.result.mark": "Mark as a result",
      "pipeline.result.mark.title": "Say that this step is one of the project's results. The pipeline behind it is then read off the inputs — no member is listed by hand, so nothing here can go stale.",
      "pipeline.result.unmark": "Not a result after all",
      "pipeline.result.unmark.title": "Takes that line back out of the project note. The step, its inputs and everything downstream stay exactly as they are; what changes is where the pipeline gets traced back from.",
      "pipeline.result.prompt": "What is this the result of? One line — it travels with the result wherever the pipeline is shown, including the figure and the Methods draft:",
      "pipeline.result.multi": "Several results, one pipeline: it holds whatever any of them needed. Each step also says which results reach it, so a single figure can still be traced on its own.",

      /* 「这一步为什么在流程里」。闭包是算出来的，算出来的东西必须能解释自己，
         否则人只能相信它——而相信一个看不见的推导，正是上一代系统的死法。 */
      "pipeline.why.result": "the declared result",
      "pipeline.why.input": "{id} declares this step's output as an input",
      "pipeline.why.parent": "{id} declares no inputs, so the step it came after stands in",
      "pipeline.why.include": "kept in by hand",

      /* 个别步骤上的例外。和 `branch:` 同一个套路：写在**那一步自己**的 note.md 上，
         不是在项目上列一张清单。文案要点出这一点，不然人会去项目页找清单。 */
      "pipeline.include.badge": "kept in",
      "pipeline.include.badge.title": "This step says it belongs to the pipeline even though tracing the inputs never reaches it. It declares that about itself, the same way a candidate does — the project never holds a list.",
      "pipeline.exclude.badge": "left out",
      "pipeline.exclude.badge.title": "This step says it is not part of the pipeline. The trace reaches it and it worked; it is still not part of how the result is made. Exploratory work usually isn't.",
      "pipeline.badge": "in the pipeline",
      "pipeline.badge.title": "This step is part of how a declared result came about. Nothing was ticked to say so — it is where following the inputs back from the result arrives.",
      /* 两条路径必须能互相跳。「这一步当时有 3 个候选，为什么选了它」正是
         两条都留着的意义，所以这两句 tooltip 说的是**去那边能看到什么**，
         不是「切换视图」。 */
      "pipeline.jump.dev": "Show it on the development path",
      "pipeline.jump.dev.title": "The pipeline shows the road that was taken; the development path shows the ones that weren't. If this step was one of three candidates, that is where the other two are, and why this one carried on.",
      "pipeline.jump.final": "Show it in the final pipeline",
      "pipeline.jump.final.title": "Where this step sits in the chain that produced the result, and what runs on either side of it.",

      /* 三条白拿的判断。第二条是最严重的发现，措辞要让人当回事而不像在指责：
         「结果依赖着一条自己已经放弃的路」多半是标记过期了，也可能真是那样，
         两种都该在投稿前自己发现，而不是从审稿人嘴里听到。 */
      "pipeline.check.head": "Worth knowing before anyone else follows this",
      "pipeline.warn.noresult": "No step is declared a result, so there is nothing to compile a pipeline from yet. One line in the project note is the whole of it.",
      "pipeline.warn.dead": { one: "The pipeline runs through a step marked dead: {ids}. The result rests on a road you gave up on yourself. Either the mark is out of date and the road did carry on, or the dependency is real and the result stands on a dead end — either way, better found here than by a reviewer.",
                              other: "The pipeline runs through {n} steps marked dead: {ids}. The result rests on roads you gave up on yourself. Either those marks are out of date and the roads did carry on, or the dependencies are real and the result stands on dead ends — either way, better found here than by a reviewer." },
      "pipeline.warn.weak": { one: "{ids} is in the pipeline and sits at L0 or L1, which means nobody outside this project can run it. The pipeline can be no stronger than that step, so it is the one to mend before this goes out.",
                              other: "{n} steps in the pipeline sit at L0 or L1 — {ids}. Nobody outside this project can run those, and the pipeline can be no stronger than its weakest one, so they are what to mend before this goes out." },
      /* trace_core 发的是**七**条 pipeline 诊断，上面只有三条。少一条的后果不是
         「没翻译」而是**英文界面上原样漏出一整句中文**——warnText 认不出 code 就
         退回服务端原句（那条退路是对的：绝不吞掉警告），于是缺的那几条恰好是最
         需要人读懂的四条自相矛盾。所以这四条和上面那三条是同一批东西，不是补充。 */
      "pipeline.warn.excluded.consumed": { one: "{id} says `pipeline: exclude`, yet {ids} — which is in the pipeline — reads what it produced. Both cannot be true: either that exclude line is stale and the step is part of the method after all, or the `input:` on {ids} points at the wrong step.",
                                           other: "{id} says `pipeline: exclude`, yet {n} steps in the pipeline read what it produced — {ids}. Both cannot be true: either that exclude line is stale and the step is part of the method after all, or those `input:` lines point at the wrong step." },
      "pipeline.warn.excluded.result": "{id} is declared a result in the project note and also carries `pipeline: exclude` on itself. It is kept in — a pipeline without its endpoint is not a pipeline — but one of those two lines is out of date, and only you know which.",
      "pipeline.warn.cycle": { one: "{ids} depends on its own output, so there is no first step to put in a Methods section. The pipeline is still shown, with the cycle placed last by id.",
                               other: "The data dependencies among {ids} form a cycle, so no order of {n} steps can be right. The pipeline is still shown, with the cycle placed last by id — but the question \"which of these comes first\" has no answer in the notes yet, and it needs one before this is written up." },
      "pipeline.warn.dangling": "`result: {id}` points at a step that does not exist, so nothing can be traced back from it. Usually a mistyped id, or the step was deleted — deletions are recorded under the project note's own section.",
      /* `pipeline:` 写错值时 core 的降级提醒（当没写、继续建树）。和 bad_branch
         同一条路，缺了它同样是英文界面漏中文。 */
      "lint.pipeline.unknown": "`pipeline: {pipeline}` is not a value this understands, so the line is being ignored. It takes `include` or `exclude`, and a reason after the pipe.",

      /* 整条流程的等级 = 其中最弱的一步。一个数回答「别人能不能照着做出来」，
         所以每一级都要把那个数翻译成一句关于**别人**的话。 */
      "pipeline.level.head": "How far this pipeline can be followed",
      "pipeline.level.note": "A chain is worth what its weakest step is worth: one step nobody can locate stops the whole thing, however carefully the rest was written.",
      "pipeline.level.weakest": "{link} {title} is what holds the whole pipeline at this level",
      "pipeline.level.means.L0": "As it stands a reader cannot even follow what was done — somewhere along the chain the judgement itself was never written down.",
      "pipeline.level.means.L1": "A reader can follow the reasoning but could not repeat the work: somewhere in the chain the code or the artifacts have no recorded location.",
      "pipeline.level.means.L2": "Everything the chain used can be found again, code and artifacts both. Whether it still runs is untested.",
      "pipeline.level.means.L3": "Someone has confirmed that the commands, the environment and the seeds are all there, end to end. A reader has what they need to try.",
      "pipeline.level.means.L4": "Every step has been re-run and the numbers came back. This is as good as a record gets.",

      /* 导出。三样都从同一份派生来，所以它们不可能互相矛盾；逐字节确定意味着
         「重新生成」永远比「留一份拷贝」便宜——这一点值得在界面上说出来。
         最后那句「这是初稿」是硬要说的：Methods 草稿是给人改的骨架，
         把记录里已有的事实排好而已，它不会替你写一句论文腔的话。 */
      "export.head": "Hand this to someone",
      "export.lead": "All three are compiled from the same derivation, so they cannot contradict each other; and the same record compiles to the same bytes every time, so regenerating always beats keeping a copy around.",
      "export.figure": "Figure",
      "export.figure.note": "One self-contained SVG of the pipeline — nothing to fetch, no script to run, and readable in black and white. Journals print in grey far more often than anyone plans for.",
      "export.methods": "Methods draft",
      "export.methods.note": "The steps in order: what was done, the command as it ran, where the code is, and each artifact with its checksum. The facts you already recorded, arranged in the shape of a Methods section.",
      "export.page": "Standalone page",
      "export.page.note": "The pipeline as one page a collaborator can open with the network off. It holds the final pipeline only — the dead ends stay at home.",
      "export.draft.note": "A draft, deliberately. It sets down what the record actually says and leaves the prose to you: nothing here invents a sentence for a fact nobody wrote down, so read every line before this goes anywhere.",

      /* ---- ⑨ 章节：一个项目内部并列的几块 ----
         磁盘上只多了一行 `chapter:`，写在**开启那条线的那一步**上，沿树继承。
         「这一章有谁」「这一章的定稿流程」「这一章的等级」全是扫出来现算的，一个字不存。
         文案要挡的是同一个误会：**章节不是分叉**。分叉是互斥候选（只能选一条走下去），
         章节是并列的几块（每一块都要留、都要写进论文）。两者混起来的后果是人拿章节
         去表达「这条路我放弃了」，那会让一整块工作在图上看起来像被否掉了。

         **占位符的规矩**：页面自己拼的那些文案里，`{chapter}` 永远是章节名——
         `{name}` 已经被 toast.export.ready 占着当「导出的那样东西」了，两个意思
         共用一个名字，接线时把哪个塞进哪个只能靠猜。
         `lint.chapter.*` 那几条是**例外，而且必须是例外**：warnText 把 core 的
         `w.vars` 原样喂进来（app.js 的 WARN_MAP.take 不改名），所以那里的占位符
         只能逐字用 core 的变量名（name / names / ids / id / note）。改成 chapter
         的后果不是少个词，是整条退回去显示服务端那句中文。 */
      "chapter.name": "Chapter",
      "chapter.lead": "A chapter is one of the parts a project is made of — the main experiment, the ablations, the data preparation. Each part gets its own line of work, its own results, and its own paragraph in the paper.",
      /* 这一句是整块改动的关键。项目 / 章节 / 分叉是最容易混的三样，而混错的方向
         是固定的：人会拿章节去表达分叉。所以「不互斥」这三个字必须出现。 */
      "chapter.vs.note": "Three things that look alike and aren't. A **project** is a different piece of research. A **chapter** is one of several parts of the same research — and the parts do not compete: the ablations and the main experiment are both kept, and both get written up. An **alternative** is two answers to one question, and only one road is taken. Reach for a chapter when both roads stay; reach for an alternative when only one can.",
      "chapter.head": { one: "Chapter · {n}", other: "Chapters · {n}" },
      "chapter.entry": "{chapter} — {what}",
      "chapter.entry.bare": "{chapter}",
      "chapter.badge.title": "Which part of the project this step belongs to. A chapter is declared once, on the step that opens the line of work, and everything below inherits it — so this badge is usually read off an ancestor rather than written here.",
      /* 「未分章」是绝大多数项目的状态，措辞绝对不能像缺了什么。人一旦觉得这里
         有个洞，就会去给每一步都补一个章节名——那正是继承要省掉的事。 */
      "chapter.none": "unchaptered",
      "chapter.none.title": "Most projects are one line of work and never need dividing; these steps simply belong to the project. Chapters are for when two parts of the same research want paths of their own. Nothing is missing here.",
      "chapter.steps": "{steps} · done {done} / wip {wip} / dead {dead}",
      /* core 管这些叫 `roots`：章节的**入口**，也就是 parent 不在同一章的那些成员
         （真正的树根算，从别的章分出来的第一步也算）。一个章节有好几个入口是常态，
         因为它可以横跨好几棵树——所以这条不能说成「它有几个根」。 */
      "chapter.roots": { one: "{n} way in", other: "{n} ways in" },
      "chapter.roots.title": "A chapter is a set of steps, not a subtree. It can start in several places at once — a root of its own here, a step split off from another chapter there — and still be one chapter. What holds it together is the name, not the shape of the tree.",
      "chapter.of.head": "Part of {chapter}",
      "chapter.of.lead": "This step belongs to one part of the project, and the other parts carry on beside it. A chapter is a division of labour, not a road that wasn't taken.",

      /* 继承是这套东西最省事的地方，也最需要被说出来：不说，人会给二十步各标一遍，
         然后改一次章节名要动二十个文件。 */
      "chapter.declared": "the chapter starts here",
      "chapter.declared.title": "This is the step the chapter was declared on. Every step below it belongs to the same chapter without saying a word, which is why renaming a chapter is a one-line edit in one file.",
      "chapter.inherited": "inherited from {id}",
      "chapter.inherited.title": "Nothing about the chapter is written on this step. It comes down from {id}, where this line of work started — that is how twenty steps end up in one chapter without twenty lines saying so.",
      "chapter.inherit.note": "Declare it once, on the step where a line of work **starts**, and everything growing out of it belongs there too. Twenty ablation steps need one line in one file — and renaming the chapter later means editing that line, not twenty.",
      "chapter.leave.note": "To take a step and its subtree out of the chapter they inherited, declare a new one **on that step**. There is no way to say \"none\": belonging to no chapter is what happens when neither a step nor any of its ancestors ever declared one.",
      "chapter.desc.label": "What this chapter is",
      "chapter.desc.title": "The text after the pipe describes the chapter itself, not this step. Where several steps open the same chapter, the earliest one by id supplies the description that shows.",
      "chapter.desc.missing": "Nobody has said what this chapter is. The name usually carries it, so the line is optional — worth writing when the name alone could be read two ways.",
      /* 两件**故意没做**的事。不写出来，后来的人会以为是漏了，然后去实现它。 */
      "chapter.ids.note": "Ids are not renumbered per chapter: the ablations do not start again at 001. An id records when a step was created, not where it sits — and a [[007]] in someone else's note, or a footnote in a paper, has to mean one step across the whole project.",
      "chapter.nest.note": "Chapters do not nest. A slash in the name groups the display, so \"main/data prep\" sits under \"main\" on screen; underneath it is still one flat set of names, and nothing reads the slash as a parent.",
      "chapter.group.title": "Grouped by the slash in the name. That is a convenience of the display only — underneath, the chapters are a flat set.",

      /* 白拿的三处：各自的定稿流程、各自的等级、跨章节的边。 */
      "chapter.pipeline.head": "Final pipeline · {chapter}",
      "chapter.pipeline.note": "Every chapter compiles a pipeline of its own: a declared result names a step, that step sits in a chapter, and the chain is traced back from there. It is how a paper is built anyway — the main experiment is one Methods paragraph and the ablations are another.",
      "chapter.pipeline.none": "No step in {chapter} is declared a result, so there is nothing to compile a pipeline from here yet. One line in the project note — `result: 031 | the ablation in figure 4` — gives this chapter a chain of its own, a weakest step of its own, and a Methods draft of its own.",
      "chapter.level.head": "How far {chapter} can be followed",
      "chapter.level.note": "Read for this chapter alone, and worth exactly what its weakest step is worth. It answers the question a reader actually has about one part of the work — could somebody else redo the ablations — without the rest of the project raising or lowering the answer.",
      "chapter.level.weakest": "{link} {title} is what holds this chapter at its level",
      /* 跨章节的边正是这块设计的收益：它说的是「消融是对着主结果测的」。
         藏起来的话，两块工作在图上看着毫无关系，而它们的关系恰恰是重点。 */
      "chapter.cross.head": { one: "Edge into another chapter · {n}", other: "Edges into another chapter · {n}" },
      "chapter.cross.note": "These are drawn, not tidied away. An input reaching across chapters is the sentence \"the ablations were measured against the main result\" — the most telling edge in a project is often the one that leaves its own part of it.",
      "chapter.cross.entry": "{link} · {chapter} — {what}",
      "chapter.cross.entry.bare": "{link} · {chapter}",
      "chapter.cross.input": "reads across from {chapter}",
      "chapter.cross.input.title": "The bytes this step consumed were produced in another chapter, and that is usually the whole point: an ablation is an ablation because it eats the main experiment's artifacts and changes one thing about them.",
      "chapter.cross.parent": "branched off {chapter}",
      "chapter.cross.parent.title": "This line of work grew out of a step in another chapter. It says where the thinking was picked up — the ablations were not started from nothing, they were split off from the main experiment at a particular point.",
      "chapter.cross.legend": "crosses chapters — an edge whose two ends sit in different parts of the project",

      /* 章节的四条诊断。**一条都不影响可溯源等级**——它们问的是「这几块讲清楚了
         没有」，不是「这个结果追不追得到」。所以每一条自己都要说一遍不影响等级：
         lint.note 那句总说明会被当背景板略过，而这几条最容易被误读成「我判低了」。
         key 名对着 core 的 code：chapter_note_conflict / chapter_no_result /
         chapter_near_duplicate / bad_chapter。占位符也只用 core 结构化放进 vars 的
         那几个名字（name / ids / id / names / note），绝不从中文句子里抠。 */
      "lint.chapter.desc.conflict": "{name} is opened in more than one place — {ids} — and the descriptions given there don't agree. The one on {id} is shown, being the earliest by id; the others appear nowhere at all. Usually a typo, or two people each writing their own. Keep one of them, or give the other its own chapter name. The level is untouched.",
      /* core 只在**项目里别处已经有成果**时才发这一条：一个 result: 都没有的项目，
         pipeline_no_result 已经说过一遍了，再按章节念 N 遍就是让人从此不看诊断。
         所以这句话可以直说「别的章节有」——它成立。 */
      "lint.chapter.noresult": "Nothing in {name} is declared a result, so this part of the project can't compile a pipeline of its own, while the other parts can. Not a defect — a chapter is free to be exploratory. It is worth a line of its own in the project note if this part is ever meant to reach a Methods section: `result: 031 | the ablation in figure 4`. The level is untouched.",
      /* 「只有一个步骤的章节 → 也许是笔误」**没有做**，这里也就没有它的文案。
         一个章节被开启的那一刻必然只有一步（就是声明它的那一步），那条诊断会在
         每一次正确使用时当场响一声，而人从会误报的诊断学到的是忽略整个诊断栏。
         合法的单步章节本来也存在（一步就说完的「数据准备」）。真正想逮的那件事
         由下面这条接手：只差大小写或空白的两个名字，那才是同一个章节被拆成两半。 */
      "lint.chapter.nearduplicate": "{names} differ only in case or spacing, which almost certainly makes them one chapter split in two. Each half counts its own members and exports its own Methods paragraph, and both halves look right on screen. A chapter name is matched byte for byte, so settle on one spelling. The level is untouched.",
      /* `chapter:` 写坏的唯一形状：只写了竖线右边的说明，没写名字。那一行看着像
         声明了一个章节，实际一个字都不生效——这一步静静继承 parent 的章节，人却
         以为自己开了一条新线。和 bad_branch / bad_pipeline 同一条路：报一声、
         当没写、继续建树，那半句说明原样留在文件里。 */
      "lint.chapter.unnamed": "This `chapter:` line has only the text after the pipe — \"{note}\" — and no chapter name, so it declares nothing and the step goes on inheriting whatever its parent belongs to. The form is `chapter: ablations | what this part of the project is for`. Your half-sentence is still in the file, untouched. The level is untouched.",

      /* 详情面板上的三个动作。「开一个章节」这句话本身就在教人标在哪一步——
         按钮叫「Start a chapter here」而不是「Set chapter」，是因为后者读起来
         像每一步都该设一次。 */
      "chapter.set.act": "Start a chapter here",
      "chapter.set.act.title": "Name the part of the project this step opens. Everything that grows out of it belongs to the same part, so this is the only step that needs the line.",
      "chapter.set.prompt": "What is this part of the project called? It covers this step and everything below it that doesn't name one of its own:",
      "chapter.unset.act": "Don't start a chapter here",
      "chapter.unset.act.title": "Takes the line back off this step. It goes back to belonging wherever its parent belongs, and so does everything below it that never named one of its own.",
      "chapter.write.act": "✎ Say what this chapter is",
      "chapter.write.act.title": "One line on what this part of the project is for. It travels with the chapter rather than with this step — it lives after the pipe on the chapter line.",
      "chapter.write.prompt": "What is this chapter for? One line, describing the part of the project rather than this step:",

      /* 按章节导出：论文里本来就是两段，导出跟着分开。 */
      "export.chapter.head": "Export one chapter",
      "export.chapter.note": "A paper gives the main experiment one Methods paragraph and the ablations another, so the exports come apart the same way. Each chapter is compiled from its own results — this is a different derivation, not a whole-project export with rows filtered out.",
      "export.chapter.all": "The whole project",
      "export.chapter.one": "{chapter} only",
      "export.chapter.file": "Saved as {file}",
      "export.chapter.file.title": "The chapter name is part of the filename, so exporting the ablations never lands on top of the main experiment's Methods draft.",

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
      /* ⑦ 编辑侧只有两个输入：候选自己声明「我是候选」，分叉点自己写「在决定什么」。
         hint 要把「标成候选意味着什么」说完整——尤其是「其余的最终会被标 dead」，
         因为界面上永远不会有一个「选它」按钮：那个按钮就是双真相源。 */
      "editor.branch.label": "How this step relates to its parent",
      "editor.branch.extends": "extends — carries on from the parent",
      "editor.branch.alternative": "alternative — one road out of a question, and only one gets taken",
      "editor.branch.hint": "Calling this an alternative says the parent asked a question and this is one of the answers to it. The siblings are never told about each other: every candidate carries the mark itself, and the set is whatever you get by reading them together. When one road wins you mark the others `dead` — that, and nothing else, is what records the decision.",
      "editor.branch.note.label": "This candidate in one line · optional",
      "editor.branch.note.placeholder": "resample the minority class instead of reweighting the loss",
      "editor.decision.label": "The question this step opens · optional",
      "editor.decision.placeholder": "How should the class imbalance be handled? Only one of these roads gets taken",
      "editor.decision.hint": "This goes on the step the roads leave from, not on the roads. Two children prove there were two roads; only this line says what the choice was between — and like \"Why\", it is a line nothing can generate for you.",
      /* ⑧ 编辑侧只有一个三选一，而且默认那一档就是「别管它」：流程成员是算出来的，
         手工设的那两档是**事实**，会过期，所以 hint 要先劝人不要用，再说清什么时候
         非用不可。 */
      "editor.pipeline.label": "This step and the final pipeline",
      "editor.pipeline.auto": "work it out — follow the inputs back from the results",
      "editor.pipeline.include": "keep it in — the trace misses it, but it is part of the method",
      "editor.pipeline.exclude": "leave it out — it worked, it just isn't part of the method",
      "editor.pipeline.hint": "Almost always leave this alone: membership is traced back from the results, so it keeps itself right. The two overrides are for the cases the trace genuinely gets wrong — a step whose output was copied across by hand and never declared as an input, or a side experiment that came off but that nobody should have to re-run. Both are declared here, on the step itself; the project never holds a list of members.",
      "editor.pipeline.note.label": "Why, in one line",
      "editor.pipeline.note.placeholder": "exploratory — it worked, but nothing downstream reads it",
      /* 它**不是**可选的，写入侧不写就 400。上一版这里印着「optional」，于是人选了
         exclude、跳过这一栏、按保存，收到的是服务端一句中文报错——界面亲口许了一个
         它管不着的诺。理由必填是这个键和 `branch:` 唯一的分歧，原因就写在下面。 */
      "editor.pipeline.note.required": "This one is required. Unlike a candidate group, which is visible on the tree the moment it exists, this line leaves no trace anywhere except in what gets exported — without the half-sentence there is no telling a thought-through decision from a mis-click.",
      /* ⑨ 章节。这一栏最要紧的事是**别让人每一步都填**：填二十遍，改一次名字就要
         动二十个文件，而那正是继承想省掉的。所以 hint 第一句说的是「填在哪一步」，
         不是「这一栏是什么」。 */
      "editor.chapter.label": "The chapter this line of work belongs to",
      "editor.chapter.placeholder": "ablations",
      "editor.chapter.hint": "Fill this in on the step that **starts** a line of work, and leave it empty everywhere else — every step below inherits it. Twenty ablation steps need this line once, in one file, and renaming the chapter later touches that one file. Filling it in on a step that already inherits a chapter is how a new line splits off: the subtree under it follows the new name.",
      "editor.chapter.inherited": "inheriting {chapter} from {id} — leave this empty to stay in it",
      "editor.chapter.note.label": "What this chapter is · optional",
      "editor.chapter.note.placeholder": "take the modules out one at a time and compare against 023",
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
      /* `decision:` 和候选自己那句说明。它们和标题、正文一样是人写的散文，
         grep 找得到，站内搜索也必须找得到。 */
      "search.where.fork": "what was being decided",
      /* 命中落在译文里。没有这两条时 whereLabel 会原样显示服务端发的 "tr.title"
         ——双语这一整套功能的意义就是英文那一侧也答得出同一个问题，结果连
         「命中在哪」都还说着内部字段名。 */
      "search.where.tr.title": "translated title",
      "search.where.tr.body": "translated text",
      /* 章节名和它那句说明也是人写的散文，`grep -r "消融"` 找得到，站内搜索
         就不能找不到——否则「消融那块当时是怎么想的」得靠人一个项目一个项目翻。 */
      "search.where.chapter": "chapter name and what it is",
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
      /* 三条里最重的一条：被删的这一步是 project.md 声明的**成果**，整条定稿流程
         从它长出来。放着不管的后果不是「少一条流程」，是 id 被重用之后，下一个拿到
         这个号的步骤无声地变成论文报的那个结果。 */
      "toast.deleted.result": "Deleted {id}, which the project note declares a result — the pipeline was traced back from it and now has nothing to start from. Fix or drop that line before the id gets reused.",
      "toast.insights.saved": "Saved — only the four insight sections were replaced",
      "toast.insight.added": "Filed under \"{label}\"",
      "toast.insight.updated": "Rewrote {id} — same id, so anything pointing at it still points at it",
      "toast.insight.superseded": "{id} supersedes {old}; {old} is folded up, not gone",
      "toast.moved": "Moved {id} under {parent} — the old parent and your reason are in the note now",
      "toast.moved.root": "{id} is a root of its own now — the move is recorded in the note",
      /* 移动一个候选的直接后果。事后看不出来：组是派生的，重新读一遍只告诉你
         「现在是什么样」。不说的话，下次见到的是一条来路不明的「只有一个候选」。 */
      // 章节是继承来的，所以挪一步会把整条子树一起换章——屏幕上那几步算进
      // 哪一份 Methods 全变了，而没有任何一行 `chapter:` 被写过。必须说出来。
      "toast.moved.chapter": "chapter {from} → {to}",
      "toast.moved.chapter.also": { one: " (and {n} step below it)",
                                    other: " (and {n} steps below it)" },
      "toast.moved.fork": { one: "the fork at {at} is down to {n} candidate",
                            other: "the fork at {at} now holds {n} candidates" },
      "toast.moved.fork.roots": { one: "the candidates among the roots are down to {n}",
                                  other: "the roots now hold {n} candidates between them" },
      "toast.branch.alternative": "{id} is a candidate now — {n} roads leave {parent}, and one of them gets taken",
      "toast.branch.extends": "{id} reads as an ordinary continuation again",
      "toast.decision.saved": "The question is written on {id}",
      "toast.decision.cleared": "The question line is gone from {id}; the candidates under it are untouched",
      /* ⑧ 声明成果的直接后果看不见：流程是算出来的，界面上只会「多出一条链」。
         所以这几条 toast 要顺手说出那条链是怎么来的。 */
      "toast.result.marked": "{id} is a declared result now — the pipeline behind it is traced back from the inputs",
      "toast.result.unmarked": "{id} is no longer a declared result; nothing about the step itself changed",
      "toast.pipeline.include": "{id} is kept in the pipeline by hand — the note says so, the project holds no list",
      "toast.pipeline.exclude": "{id} is out of the pipeline; on the development path it stays exactly where it was",
      "toast.pipeline.auto": "{id} is back to being worked out from the inputs",
      "toast.export.ready": "{name} is built — the same record always compiles to the same bytes",
      "toast.export.chapter.ready": "{chapter} · {name} is built — the same record always compiles to the same bytes",
      /* ⑨ 标一个章节的后果是**整棵子树**跟着改归属，而屏幕上一棵子树和一个光杆
         节点长得一模一样（和 drag.carry 同一条理由）。所以带走了几步要说出来。 */
      "toast.chapter.set": "{id} opens {chapter} — everything below it that doesn't name one of its own belongs there too",
      "toast.chapter.carry": { one: "{n} step below comes with it", other: "{n} steps below come with it" },
      "toast.chapter.cleared": "{id} no longer opens a chapter; it belongs wherever its parent does",
      "toast.chapter.desc.saved": "Saved on {chapter} — the description belongs to the chapter, not to this step",
      // 说明落在别的步骤上时要说出来：一次保存改的不是你选中的那个文件。
      "toast.chapter.desc.saved.elsewhere": "Saved on {chapter} — the description lives on {id}, the step that opened the chapter, so that is the file that changed",
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
      "count.alternatives": { one: "{n} candidate", other: "{n} candidates" },
      "count.rejoins": { one: "{n} rejoin", other: "{n} rejoins" },
      "count.chapters": { one: "{n} chapter", other: "{n} chapters" },
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
      /* ⑧ 两条路径。它们在切换器里并排站着，所以这两个名字必须互相解释：一个回答
         「我是怎么走到这儿的」，一个回答「该怎么做」。第一次看见两个视图的人，
         能不能分清自己在看什么，全靠这两行和下面 pipeline.*.lead 那两句。 */
      "app.view.dev": "开发路径",
      "app.view.dev.title": "全部记录，含走不通的那些和还没决定的岔路口。这是给自己看的一档：它回答「我是怎么走到这儿的」。",
      "app.view.pipeline": "定稿流程",
      "app.view.pipeline.title": "只有真正产出成果的那条链，顺着声明的输入反推出来。这是给别人看的一档：它回答「该怎么做」。",
      /* ⑨ 章节的筛选器。只在项目里真有章节时才出现——一个 `chapter:` 都没写的
         项目必须完全无感，多一个恒灰的下拉框就已经不是无感了。 */
      "app.chapter.all": "所有章节",
      "app.chapter.title": "只看项目的其中一块。章节是从步骤上扫出来的，任何地方都没有一份章节清单。",
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
      "list.legend.extends": "延伸 —— 同一条路上再往下走的一步",
      "list.legend.alternative": "互斥候选 —— 同一个问题的几个答案，只能选一条走下去",
      "list.legend.rejoin": "汇回 —— 这条支线做出来的东西，被另一条线上更靠后的一步读了",
      "list.legend.extends.title": "最平常的那一种，也是本来就不该费神去想的那一种：这一步接着父步骤往下做。",
      "list.legend.alternative.title": "父步骤那里提了一个问题，这些是摆出来的答案。这一组是从树上读出来的——凡是自称候选的孩子都在组里。兄弟之间互不登记，所以永远不会出现两条记录对「谁是谁的候选」各执一词。",
      "list.legend.rejoin.title": "它根本不是树上的边，而是一条声明出来的输入，画在这里是为了让一条又绕回来的支线不至于看着像死胡同。曲线加箭头本身就说明了这件事：树上的边从来不弯。",
      "list.legend.note": "颜色负责一眼扫到，形状负责说得准。一组候选外面有一道括弧，汇回永远是带箭头的曲线——于是灰度打印出来、或者看不见颜色的人，丢掉的只是那一眼，不是那个意思。",
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

      /* ---- ①b 拖着改父节点 ----
         手势省掉的是「在下拉框里翻 id」的那十几秒，不是那句原因。所以这一组文案
         全都短——它们贴着指针走，读它们的人正在拖东西，一眼一句。
         唯一长的一句是 move.dragged.note：它要挡住手势最容易造成的误会，
         也就是「我刚才把数据流也接过去了」。 */
      "move.dragged.note": "拖拽只挑了新的父步骤，别的什么都没动。`parent` 说的是当时接着哪一步想；`inputs`（这些字节从哪来）一个字没变，笔记正文也一样。",
      "drag.aim.parent": "→ 挂到 {parent} 下面",
      "drag.aim.root": "→ 自己成为一棵树的根",
      "drag.aim.none": "落在某一步上，就挂到那一步下面",
      "drag.no.self": "不能挂到自己身上",
      "drag.no.descendant": "{parent} 是从 {id} 长出来的",
      "drag.no.noop": "本来就挂在这里",
      "drag.no.missing": "没有这一步",
      "drag.carry": "下面 {n} 步跟着一起走",
      "drag.root.label": "让它自己成为一棵树的根",
      "drag.root.hint": "只有这里算。落在别的空白处等于取消。",
      "drag.cancelled": "移动取消了——什么都没写。",
      "drag.readonly": "这一页是静态导出：记录的一张照片，不是记录本身。移动会往笔记里追加一行，只有服务端写得了。",
      "drag.flow": "这张图上的边是 inputs——这些字节从哪来。拖拽改的是 parent——当时接着哪一步想。要移动请回树视图或列表视图；这两件事是刻意分开的。",

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
      "lint.missing.why": "标了 done/dead 却没写「**为什么**」。这是唯一无法自动生成的字段——日志能自动存、commit 能自动记，只有「我当时为什么决定试这个」忘了就再也补不回来。",
      "lint.missing.what": "标了 done/dead 却没写「**做了什么**」。重跑要靠它，只有一个标题的话，别人（和半年后的你）无从下手。",
      "lint.missing.conclusion": "标了 done/dead 却没写「**结论**」——假设到底成不成立。一步走完了却没说怎么走完的，正是「死胡同没被记下来」的样子。",
      "lint.figure.nocaption": "有一张图没有图注。写成 `![](…… \"这张图说明了什么\")`。你和任何读这条记录的 agent 都只看得见这一行——没有图注，图里的结论对文本读者就是丢失的。",
      "lint.subheads": "`{section}` 下面没有自己的正文，只有子标题。这算写了——写这一行只是让你知道它是怎么被读的。",
      "lint.table.nodesc": "表格没有一句说明。只拿到一堆数字的人，只能猜哪个方向算好。",
      "lint.pre.nodesc": "代码块没有一句说明——说清它做什么，或者它的输出后来说明了什么。",
      "lint.alt.lone": "标了候选，旁边却没有别的候选——只有一条路可选，就没什么可选的。要么另一条路当时没写下来，要么这本来就只是下一步，把这个标记取掉即可。两种情况都不影响等级。",
      "lint.fork.noquestion": "这一步下面分出 {n} 条候选，却没有一行说清在决定什么。当时有几条路，树上看得见；这个岔路口是为了什么，恰恰是树装不下的那部分。只是提示，不影响等级。",
      "lint.fork.open": "这里还有 {n} 个候选活着，这个岔路口还没有答案。这不是错：同时开几条线本来就是研究的常态。列出来是因为「未决的分叉」就是你还背着的问题，而且把放弃掉的那几条标成 dead，现在做比一年以后做容易得多。不影响等级。",
      "lint.fork.nocandidates": "{id} 上写了在决定什么，底下却还没有任何一步声明自己是候选。这一行已经存下来了，只是它还不构成一个岔路口——不成组、图上不画括弧、未决清单里也不出现。给每条路各标一个 `alternative`，这个问题就立起来了。两种情况都不影响等级。",
      "lint.branch.unknown": "`branch: {branch}` 这个取值认不出来，这一步已经按普通延伸处理了——也就是说你写的那个标记现在一点作用都没有。可用的取值只有 `extends` 和 `alternative` 两个。",

      /* ---- ⑦ 决策分叉与汇回：树上的边不再只有一种含义 ----
         存储上只多了两个键（候选自己写 `branch: alternative`，分叉点自己写
         `decision:`），"这一组是互斥的""最后选了哪一个"全是扫出来的派生结论。
         文案必须把这一点说出来，否则人会去找一个"标记赢家"的按钮，找不到就以为
         功能缺了一半——而那个按钮本身就是双真相源。 */
      "branch.kind.extends": "延伸",
      "branch.kind.alternative": "互斥候选",
      "branch.kind.extends.title": "接着父步骤往下做。什么都不写的时候，一条边就是这个意思，所以它从不写进笔记。",
      "branch.kind.alternative.title": "一个问题分出来的几条路之一，而这些路是互斥的——走了这一条，就意味着放弃其余几条。每个候选只声明自己；把它们放在一起读，才得到那一组。",
      "branch.badge": "候选",
      "branch.badge.title": "这一步是同一个问题的几个答案之一。其余几个就挂在它旁边，同一个父步骤下面。",

      "decision.pick": "{n} 选 1",
      "decision.pick.title": "这个问题分出 {n} 条路，最后走一条。走的是哪条并不存在任何字段里——其余几条被标成 dead 的那一刻，活下来的那条就是答案。",
      "decision.settled": "已定：{id}",
      "decision.settled.title": "这里其余的候选都标了 dead，于是 {id} 就是当时选的那条路。没有人专门记过这件事，它是那几条 dead 加起来的结果。",
      /* 逐字是「都不行」，不是「全部作废」：README 的「三种边的视觉编码」那一行、
         FORMAT.md 15.3 的状态表、MCP 的 fork_label 三处都是这三个字，而「作废」
         听着像是这几条记录出了问题被撤销了 —— 它们没有，它们各自都是走到底的
         结论，只是这个问题的答案恰好是「都不行」（P4）。 */
      "decision.alldead": "都不行",
      "decision.alldead.title": "这个问题分出去的路全都走到了 dead。问题还立在那里，而「它至今没有答案」本身就是一条结论，不是要顺手补齐的窟窿。",
      "decision.lone": "只有一条",
      "decision.lone.title": "{id} 被标成了候选，旁边却没有别的路。一个候选不成其为选择——要么是另一条忘了标 branch: alternative，要么这条本来就只是普通的延伸。",
      "decision.open": "还没定",
      "decision.open.title": "这里还有不止一个候选活着。它说的是工作现在走到哪了，不是说哪里做错了。",
      "decision.open.note": "这里还有 {n} 条路活着，所以这个岔路口还没做决定——这是很正常的状态。等你把放弃掉的那几条标成 dead，它就自动读作「已定」；趁还记得为什么放弃，顺手标了最划算。",
      "decision.open.summary": "还有 {n} 个岔路口没做决定",
      "decision.open.summary.title": "还有不止一个候选活着的那些岔路口。隔了一个月回到一个项目，最该先看的就是这份清单——一个未决的分叉，就是一个你还背在身上的问题。",
      "decision.question.label": "在决定什么",
      "decision.question.title": "人手写在分叉的那一步上。这句话推导不出来：两个孩子只能证明当时有两条路，永远说不出这两条路是在选什么。",
      "decision.question.missing": "没有一行说清这里在选什么。几条路看得见，它们回答的那个问题看不见。",
      "decision.question.orphan": "记下来了——但底下还没有任何一步声明自己是 `branch: alternative`，所以它现在还不是一个岔路口：不成组、树上不画括弧、也不出现在「还没做决定」那份清单里。把每条路各标成一个候选，这个问题就在它们上面立起来了。",
      "decision.head": "决策点 · {n} 个候选",
      "decision.lead": "下面这几条路是同一个问题的答案，最后只有一条走得下去。选中的是哪一条不是任何地方的一个字段——它是从其余几条被标成 `dead` 读出来的，于是不存在第二份会过期的拷贝。",
      "decision.candidate.entry": "{link} —— {what}",
      "decision.candidate.entry.bare": "{link}",
      "decision.candidate.dead": "已放弃",
      "decision.candidate.live": "还活着",
      "decision.of.head": "{parent} 那个岔路口的一个候选",
      "decision.of.lead": "这一步是 {parent} 提出的那个问题的一个答案。那里的几条路最后只有一条走得下去，其余的会被标成 `dead`——而那就是「当时选了哪个」的全部记录。",
      "decision.siblings.head": "并列的其他候选 · {n}",
      "decision.siblings.lone": "这个父步骤下面没有别的候选，于是这一组里只有一条路。要么当时没走的那条压根没写下来，要么这本来就只是下一步，标记可以取掉。",
      /* 根之间也能成一组（两种互斥的开局）。它没有分叉点，于是没有任何一步能承载
         那句 `decision:`——界面得如实说出来，不然人会到处找那个写问题的地方。 */
      "decision.roots.head": "{n} 种开局，最后走一种",
      "decision.roots.note": "这几个候选都是根：它们上面没有一步可以挂那句问题，所以「在决定什么」在这里没有地方可写。把它写进每条路自己的标题里。",
      "decision.mark.act": "标成互斥候选",
      "decision.mark.act.title": "声明这一步和它的兄弟们在回答同一个问题，而且最后只会有一条被走下去。",
      "decision.unmark.act": "其实不是候选",
      "decision.unmark.act.title": "改回普通的延伸。id、笔记、兄弟节点全都原样不动，变的只是它头上那条边怎么读。",
      "decision.write.act": "✎ 写清在决定什么",
      "decision.write.act.title": "一行字说清这几条路在回答什么问题。它挂在这一步上，也就是几条路分出去的那一步。",
      "decision.write.prompt": "这里在决定什么？一行字，写成下面这几条路所回答的那个问题：",

      "rejoin.kind": "汇回",
      "rejoin.kind.title": "一条从别的路横过来的 input：在旁边那条线上做出来的东西，被这边更靠后的一步读了。它和数据流视图画的是同一条线——只是树以前一直没把它画出来。",
      "rejoin.lead": "一条支线不一定在停下的地方就结束了。它产出的东西后来被另一条线读走，那就是一次汇回——而它是一条 `input:`，不是第二种父节点。为它没有新增任何字段，这条边一直就在文件里。",
      "rejoin.out.head": "本步的产物汇回到 {n} 步",
      "rejoin.in.head": "从 {n} 条别的路汇进本步",
      "rejoin.entry": "{link} —— {what}",
      "rejoin.entry.bare": "{link}",
      /* 汇回边自带「两条路是在哪儿分开的」（core 算的 LCA）。说出这个岔路口，
         「它绕回来了」才有具体的形状——否则读者只知道有一条曲线。 */
      "rejoin.at": "两条路是在 {id} 分开的",
      "rejoin.badge": "有汇回",
      "rejoin.badge.title": "别的路上产出的东西被这里读了，或者这里产出的东西被别的路读了。图上那些弯曲的边就是它们。",

      /* ---- ⑧ 两条路径：记录与方法的分家 ----
         磁盘上只多了一行 `result:`（写在 project.md 的 front-matter 上，可重复）。
         「哪些步在流程里」「什么顺序」「整条链多可靠」全是从成果沿 `input:` 反向
         闭包算出来的，一个字都不存——存了就是一份会漂移的中心索引（P1 禁止）。
         文案必须把这件事说出来，否则人会去找一个「流程成员清单」的编辑框，
         找不到就以为功能缺了一半——而那个清单正是双真相源本身。 */
      "pipeline.name": "定稿流程",
      "pipeline.lead": "真正把成果做出来的那条链，别的都不在里面。别人照着它才能拿到同样的数字，论文的 Methods 也是从它长出来的。",
      "pipeline.dev.name": "开发路径",
      "pipeline.dev.lead": "所有步骤都在，包括走不通的那些、还有你现在背着的那几个岔路口。哪里看着不对、想查清它是怎么变成这样的，看这一条。",
      /* 这一句是两条路径全部的关键。不写它，人会把定稿流程理解成「开发路径的
         精简版」，然后开始问「为什么这一步被藏起来了」——而它们回答的根本是
         两个不同的问题。 */
      "pipeline.pair.note": "两条路径不是同一件事的详略两版。开发路径说的是**当时怎么走到那儿的**——试过什么、哪条断了、哪一刻做了选择；定稿流程说的是**该怎么做**——把这个结果做出来的、最短而且诚实的一份说明。一步完全可以在前者里极其重要，同时在后者里根本不该出现。",
      "pipeline.head": "定稿流程 · {n} 步",
      "pipeline.order.note": "按工作必须发生的顺序排：一步排在它读过的所有东西后面。互不依赖的两步之间按 id 定序，于是同一份记录每次编出来的顺序都一样，两份导出可以直接 diff。",
      "pipeline.derived.note": "成员清单一个字都没存。从每个成果出发沿 `input:` 反着走，某一步没声明输入就退回它的 `parent`（那就是它事实上接着的前一步），`dead` 的剔掉。真存一份清单的话，你第一次移动步骤它就开始说假话。",

      /* 一个 `result:` 都没声明是绝大多数项目的常态，所以这四句是这个功能的门面。
         它要说清「定稿流程是什么」「为什么值得声明」「怎么声明」——不是报错，
         也不是空荡荡一句「暂无数据」。 */
      "pipeline.empty.title": "还没有哪一步被声明成成果",
      "pipeline.empty.what": "定稿流程就是真正把某个成果做出来的那条链——从成果出发，顺着每一步声明的 `input:` 往回追，把走不通的剔掉。它不是任何东西的第二份拷贝：成员全是现算的，所以你移动一步、补一条输入、把某条支标成 dead，它自己就跟着变了。",
      "pipeline.empty.why": "声明它值那三十秒，因为这是这里唯一没人能替你算出来的事——两百步里，哪一步是你会写进论文的那一个。声明完，流程本身、它最弱的那一环、还有一份 Methods 草稿都是白拿的。",
      "pipeline.empty.how": "在项目的 front-matter 里加一行：`result: 023 | 亲和力预测 AUC 0.91`。可以写多条——一张图一条、一个论断一条。或者打开某一步，点**{act}**。",
      "pipeline.empty.act": "把某一步标成成果",

      "pipeline.result.head": "已声明的成果 · {n}",
      "pipeline.result.lead": "整个流程里只有这一条是写下来的，其余全是读出来的：哪些步在里面、什么顺序、整条链能追到多远，每次都现算。",
      "pipeline.result.entry": "{link} —— {what}",
      "pipeline.result.entry.bare": "{link}",
      "pipeline.result.badge": "成果",
      "pipeline.result.badge.title": "一个声明出来的成果：这个项目要做出来的东西之一。为了走到这里所需要的一切，就是它的流程。",
      "pipeline.result.mark": "标成成果",
      "pipeline.result.mark.title": "声明这一步是本项目的一个成果。它背后的流程随即从各步的输入反推出来——不用手工列任何成员，也就没有什么东西会过期。",
      "pipeline.result.unmark": "其实不是成果",
      "pipeline.result.unmark.title": "把那一行从项目笔记里撤掉。这一步、它的输入、它下游的一切全都原样不动，变的只是流程从哪里开始往回追。",
      "pipeline.result.prompt": "这是什么的成果？一行字——它会跟着这个成果出现在流程的每个出口，包括那张图和 Methods 草稿：",
      "pipeline.result.multi": "多个成果共用一条流程：任何一个成果用到的步都在里面。每一步还会标出它被哪几个成果追到，所以单独一张图也照样追得清。",

      /* 「这一步为什么在流程里」。闭包是算出来的，算出来的东西必须能解释自己，
         否则人只能选择相信它——而相信一个看不见的推导，正是上一代系统的死法。 */
      "pipeline.why.result": "它就是声明的那个成果",
      "pipeline.why.input": "{id} 把本步的产物声明成了输入",
      "pipeline.why.parent": "{id} 没声明输入，于是退回它接着的前一步",
      "pipeline.why.include": "人手留下的",

      /* 个别步骤上的例外。和 `branch:` 同一个套路：写在**那一步自己**的 note.md 上，
         不是在项目上列一张清单。文案要点出这一点，不然人会去项目页找那张清单。 */
      "pipeline.include.badge": "留在流程里",
      "pipeline.include.badge.title": "顺着输入反推追不到这一步，而它自己声明它属于这条流程。这句话写在它自己身上，和候选声明自己是候选是同一个套路——项目那一侧永远没有清单。",
      "pipeline.exclude.badge": "不算流程",
      "pipeline.exclude.badge.title": "反推追得到这一步，它当时也成功了，可它仍然不是「这个结果是怎么做出来的」的一部分。探索性的工作多半都是这样。",
      "pipeline.badge": "在定稿流程里",
      "pipeline.badge.title": "这一步是某个声明成果做出来的过程的一环。没有人勾选过它——顺着输入从成果往回追，追到的就是它。",
      /* 两条路径必须能互相跳。「这一步当时有 3 个候选，为什么选了它」正是
         两条都留着的意义，所以这两句 tooltip 说的是**去那边能看到什么**，
         不是「切换视图」。 */
      "pipeline.jump.dev": "回开发路径上看它",
      "pipeline.jump.dev.title": "流程画的是走了的那条路，开发路径画的是没走的那些。这一步当时要是三个候选之一，另外两个就在那边，选它的理由也在那边。",
      "pipeline.jump.final": "到定稿流程里看它",
      "pipeline.jump.final.title": "看它在产出成果的那条链上排在哪，前后各是哪一步。",

      /* 三条白拿的判断。第二条是最严重的发现，措辞要让人当回事而不像在指责：
         「结果依赖着一条自己已经放弃的路」多半是标记过期了，也可能真就是那样，
         两种都该在投稿前自己发现，而不是从审稿人嘴里听到。 */
      "pipeline.check.head": "让别人照着做之前，这几件事得先知道",
      "pipeline.warn.noresult": "还没有哪一步被声明成成果，所以暂时没有可以编出来的流程。项目笔记里加一行就是全部工作。",
      "pipeline.warn.dead": "流程里有 {n} 步是 dead：{ids}。也就是说这个结果压在你自己已经放弃的路上。要么是那个标记过期了、这条路后来其实走通了，要么这条依赖是真的、结果确实立在一条死胡同上——哪一种，都该在这里发现，而不是从审稿人那里。",
      "pipeline.warn.weak": "流程里有 {n} 步停在 L0/L1：{ids}。这个项目之外的人跑不起来它们，而整条流程不会强过其中最弱的一步——投稿前要补的就是这几步。",
      /* trace_core 发的是**七**条 pipeline 诊断，上面只有三条。少一条的后果不是
         「没翻译」而是**英文界面上原样漏出一整句中文**——warnText 认不出 code 就
         退回服务端原句（那条退路是对的：绝不吞掉警告），于是缺的那几条恰好是最
         需要人读懂的四条自相矛盾。所以这四条和上面那三条是同一批东西，不是补充。 */
      "pipeline.warn.excluded.consumed": "{id} 自己写着 `pipeline: exclude`（不进流程），可流程里有 {n} 步正吃着它的产物：{ids}。这两句话不能同时成立：要么那行 exclude 已经过期、它其实是流程的一环，要么那几行 `input:` 指错了步骤。",
      "pipeline.warn.excluded.result": "{id} 既被项目笔记声明成成果，自己又写着 `pipeline: exclude`。按「成果永远在流程里」处理了（否则这条流程连终点都没有），但那两行字里一定有一行是旧的，只有你知道是哪一行。",
      "pipeline.warn.cycle": "{ids} 这 {n} 步的数据依赖成环，排不出先后。流程照样给出来（环上的按 id 序排在最后），但「先做哪一步」这个问题在记录里本身就还没有答案，写进 Methods 之前得先把环拆掉。",
      "pipeline.warn.dangling": "`result: {id}` 指向一个不存在的步骤，从它追不出任何东西。多半是 id 写错了，或者那一步被删了——删除记录在项目笔记自己那一节里。",
      /* `pipeline:` 写错值时 core 的降级提醒（当没写、继续建树）。和 bad_branch
         同一条路，缺了它同样是英文界面漏中文。 */
      "lint.pipeline.unknown": "`pipeline: {pipeline}` 不是认得的取值，这一行按没写处理。它只收 `include` 或 `exclude`，竖线后面再写一句为什么。",

      /* 整条流程的等级 = 其中最弱的一步。一个数回答「别人能不能照着做出来」，
         所以每一级都要把那个数翻译成一句关于**别人**的话。 */
      "pipeline.level.head": "这条流程能被追到多远",
      "pipeline.level.note": "一条链值多少，看它最弱的那一步值多少：只要有一步谁都定位不到，其余写得再细也停在那里。",
      "pipeline.level.weakest": "把整条流程压在这一级上的是 {link} {title}",
      "pipeline.level.means.L0": "照现在这样，读者连当时做了什么都跟不下来——链上某处，那个判断本身就没写下来。",
      "pipeline.level.means.L1": "读者看得懂当时的判断，但重做不了：链上某处的代码或者产物没有记下位置。",
      "pipeline.level.means.L2": "这条链用到的东西都找得回来，代码和产物都在。至于现在还跑不跑得起来，没人验过。",
      "pipeline.level.means.L3": "有人从头到尾确认过命令、环境、随机种子都齐了。读者手上的东西够他去试一次。",
      "pipeline.level.means.L4": "每一步都有人真跑过，数字对上了。记录能做到的就是这样了。",

      /* 导出。三样都从同一份派生来，所以它们不可能互相矛盾；逐字节确定意味着
         「重新生成」永远比「留一份拷贝」便宜——这一点值得在界面上说出来。
         最后那句「这是初稿」是硬要说的：Methods 草稿是给人改的骨架，
         把记录里已有的事实排好而已，它不会替你写一句论文腔的话。 */
      "export.head": "拿去给别人",
      "export.lead": "三样都是从同一份派生编出来的，所以它们不可能互相矛盾；同一份记录每次编出来逐字节一致，于是「重新生成」永远比「留一份拷贝」划算。",
      "export.figure": "图",
      "export.figure.note": "一张自包含的流程 SVG——不取任何外部资源、不跑脚本，黑白打印也读得出来。期刊印成灰的次数，比谁预想的都多。",
      "export.methods": "Methods 草稿",
      "export.methods.note": "按流程顺序：每一步做了什么、当时跑的完整命令、代码在哪、产物路径和校验和。都是你已经记下来的事实，只是排成了 Methods 的骨架。",
      "export.page": "独立页面",
      "export.page.note": "把流程做成一页，合作者断网也打得开。里面只有定稿流程——走不通的那些留在家里。",
      "export.draft.note": "它是初稿，而且是故意做成初稿的。它只把记录里确实写着的东西摆出来，句子留给你写：这里不会为一件没人记过的事凭空造一句话，所以发出去之前请把每一行都读一遍。",

      /* ---- ⑨ 章节：一个项目内部并列的几块 ----
         磁盘上只多了一行 `chapter:`，写在**开启那条线的那一步**上，沿树继承。
         「这一章有谁」「这一章的定稿流程」「这一章的等级」全是扫出来现算的，一个字不存。
         文案要挡的是同一个误会：**章节不是分叉**。分叉是互斥候选（只能选一条走下去），
         章节是并列的几块（每一块都要留、都要写进论文）。两者混起来的后果是人拿章节
         去表达「这条路我放弃了」，那会让一整块工作在图上看起来像被否掉了。

         **占位符的规矩**：页面自己拼的那些文案里，`{chapter}` 永远是章节名——
         `{name}` 已经被 toast.export.ready 占着当「导出的那样东西」了，两个意思
         共用一个名字，接线时把哪个塞进哪个只能靠猜。
         `lint.chapter.*` 那几条是**例外，而且必须是例外**：warnText 把 core 的
         `w.vars` 原样喂进来（app.js 的 WARN_MAP.take 不改名），所以那里的占位符
         只能逐字用 core 的变量名（name / names / ids / id / note）。改成 chapter
         的后果不是少个词，是整条退回去显示服务端那句中文。 */
      "chapter.name": "章节",
      "chapter.lead": "章节是一个项目内部并列的几块：主实验、消融实验、数据准备。每一块有自己的探索路径、自己的成果，在论文里也各占一段。",
      /* 这一句是整块改动的关键。项目 / 章节 / 分叉是最容易混的三样，而混错的方向
         是固定的：人会拿章节去表达分叉。所以「不互斥」这三个字必须出现。 */
      "chapter.vs.note": "三样长得像、其实不是一回事。**项目**是不同的研究；**章节**是同一个研究里并列的几块，它们**不互斥**——消融和主实验都要留着、都要写进论文；**分叉**（互斥候选）是同一个问题的两个答案，只能选一条走下去。两条路都要留的时候用章节，只能留一条的时候用候选。",
      "chapter.head": "章节 · {n}",
      "chapter.entry": "{chapter} —— {what}",
      "chapter.entry.bare": "{chapter}",
      "chapter.badge.title": "这一步属于项目的哪一块。章节只在开启那条线的那一步声明一次，底下整棵子树继承，所以这块牌子多半是从某个祖先读来的，不是写在这一步身上的。",
      /* 「未分章」是绝大多数项目的状态，措辞绝对不能像缺了什么。人一旦觉得这里
         有个洞，就会去给每一步都补一个章节名——那正是继承要省掉的事。 */
      "chapter.none": "未分章",
      "chapter.none.title": "绝大多数项目就是一条线走下来的，从来不需要分块，这些步骤直接属于这个项目。章节是给「同一个研究里两块各走各的」准备的。这里没有缺任何东西。",
      "chapter.steps": "{steps} · done {done} / wip {wip} / dead {dead}",
      /* core 管这些叫 `roots`：章节的**入口**，也就是 parent 不在同一章的那些成员
         （真正的树根算，从别的章分出来的第一步也算）。一个章节有好几个入口是常态，
         因为它可以横跨好几棵树——所以这条不能说成「它有几个根」。 */
      "chapter.roots": "{n} 个入口",
      "chapter.roots.title": "章节是一组步骤，不是一棵子树。它可以同时从好几个地方开始——这边一个自己的根，那边一步从别的章分出来的——照样是同一个章节。把它们绑在一起的是那个名字，不是树的形状。",
      "chapter.of.head": "属于 {chapter}",
      "chapter.of.lead": "这一步属于项目的其中一块，别的几块在旁边各走各的。章节是分工，不是没走的那条路。",

      /* 继承是这套东西最省事的地方，也最需要被说出来：不说，人会给二十步各标一遍，
         然后改一次章节名要动二十个文件。 */
      "chapter.declared": "章节从这里开始",
      "chapter.declared.title": "这一步就是声明章节的地方。它底下每一步不用写一个字就属于同一章，所以改章节名是改一个文件里的一行，不是改二十处。",
      "chapter.inherited": "继承自 {id}",
      "chapter.inherited.title": "这一步身上一个字都没写。章节是从 {id} 传下来的，那是开启这条线的地方——二十步归进同一章靠的就是这件事，而不是二十行声明。",
      "chapter.inherit.note": "在**开启那条线的那一步**声明一次，长在它下面的整棵子树都属于它。二十步的消融只要一个文件里的一行；以后要改章节名，改的也是那一行，不是二十处。",
      "chapter.leave.note": "想让某一步（连同它下面）脱离继承来的章节，就在**那一步身上**声明一个新的。没有「置空」这种写法：不属于任何章节，是它和它的祖先谁都没声明过时的那种状态。",
      "chapter.desc.label": "这个章节是什么",
      "chapter.desc.title": "竖线右边说的是这个章节本身，不是这一步。同一个章节可能被好几处声明，显示的是 id 序最早的那一处带的说明。",
      "chapter.desc.missing": "没人说过这个章节是什么。名字本身多半已经够了，所以这一行是可选的——名字有两种读法的时候才值得补一句。",
      /* 两件**故意没做**的事。不写出来，后来的人会以为是漏了，然后去实现它。 */
      "chapter.ids.note": "id 不按章节重编号：消融不会从 001 重新开始。id 说的是这一步什么时候建的，不是它在哪一块里排第几——别人笔记里的 [[007]]、论文脚注里的引用，在整个项目里都只能指一步。",
      "chapter.nest.note": "章节不嵌套。名字里的斜杠只影响显示分组，「主实验/数据准备」在屏幕上收在「主实验」下面；底下仍然是平的一层名字，没有谁把斜杠读成父子。",
      "chapter.group.title": "按名字里的斜杠分的组。这只是显示上的方便，底下的章节是平的一层。",

      /* 白拿的三处：各自的定稿流程、各自的等级、跨章节的边。 */
      "chapter.pipeline.head": "定稿流程 · {chapter}",
      "chapter.pipeline.note": "每个章节各编一条自己的定稿流程：一条 `result:` 指着某一步，那一步在哪一章，这条流程就属于哪一章，链子从那里往回追。论文里本来就是这样——主实验一段 Methods，消融另一段。",
      "chapter.pipeline.none": "{chapter} 里还没有哪一步被声明成成果，所以这一章暂时编不出流程。在项目笔记里写一行 `result: 031 | 图 4 的消融`，这一章就有了自己的链子、自己最弱的那一步和自己的 Methods 草稿。",
      "chapter.level.head": "{chapter} 能被别人追到哪一步",
      "chapter.level.note": "只按这一章算，值多少全看它里面最弱的那一步。它回答的正是读者对某一块真正会问的问题——消融这部分别人能不能重做——而不被项目其余部分抬高或压低。",
      "chapter.level.weakest": "{link} {title} 把这一章压在了这个等级上",
      /* 跨章节的边正是这块设计的收益：它说的是「消融是对着主结果测的」。
         藏起来的话，两块工作在图上看着毫无关系，而它们的关系恰恰是重点。 */
      "chapter.cross.head": "跨章节的边 · {n}",
      "chapter.cross.note": "这些边要画出来，不收起来。一条跨章节的输入说的正是「消融是对着主结果测的」——整个项目里最有话说的那条边，往往就是离开自己这一块的那条。",
      "chapter.cross.entry": "{link} · {chapter} —— {what}",
      "chapter.cross.entry.bare": "{link} · {chapter}",
      "chapter.cross.input": "读的是 {chapter} 的产物",
      "chapter.cross.input.title": "这一步吃进去的字节是另一章产出的，而这多半正是重点：消融之所以是消融，就是因为它吃着主实验的产物，只改一样东西。",
      "chapter.cross.parent": "从 {chapter} 分出来的",
      "chapter.cross.parent.title": "这条线是从另一章的某一步长出来的。它说的是想法从哪儿接过来的——消融不是从零起的，是在某个点上从主实验分出去的。",
      "chapter.cross.legend": "跨章节 —— 两端分属项目里不同块的边",

      /* 章节的四条诊断。**一条都不影响可溯源等级**——它们问的是「这几块讲清楚了
         没有」，不是「这个结果追不追得到」。所以每一条自己都要说一遍不影响等级：
         lint.note 那句总说明会被当背景板略过，而这几条最容易被误读成「我判低了」。
         key 名对着 core 的 code：chapter_note_conflict / chapter_no_result /
         chapter_near_duplicate / bad_chapter。占位符也只用 core 结构化放进 vars 的
         那几个名字（name / ids / id / names / note），绝不从中文句子里抠。 */
      "lint.chapter.desc.conflict": "{name} 在 {ids} 上各写了一句说明，彼此不一致。生效的是 id 序最早的那句（{id} 写的），其余几句在界面上一个字都不会出现。多半是笔误，或者两个人各写各的。留一句；本来就想说两个章节的话，把名字改开。不影响等级。",
      /* core 只在**项目里别处已经有成果**时才发这一条：一个 result: 都没有的项目，
         pipeline_no_result 已经说过一遍了，再按章节念 N 遍就是让人从此不看诊断。
         所以这句话可以直说「别的章节有」——它成立。 */
      "lint.chapter.noresult": "{name} 里一条 `result:` 都没有，所以这一块推不出自己的定稿流程，而项目里别的章节推得出。这不是缺陷，一个章节完全可以只是探索性的；列在这里，是因为想让这一块将来进 Methods 的话，它值得在项目笔记里有自己的一行：`result: 031 | 图 4 的消融`。不影响等级。",
      /* 「只有一个步骤的章节 → 也许是笔误」**没有做**，这里也就没有它的文案。
         一个章节被开启的那一刻必然只有一步（就是声明它的那一步），那条诊断会在
         每一次正确使用时当场响一声，而人从会误报的诊断学到的是忽略整个诊断栏。
         合法的单步章节本来也存在（一步就说完的「数据准备」）。真正想逮的那件事
         由下面这条接手：只差大小写或空白的两个名字，那才是同一个章节被拆成两半。 */
      "lint.chapter.nearduplicate": "{names} 只差大小写或空白，几乎肯定是同一个章节被拆成了两半。两半各算一份成员、各导出一段 Methods，而两边看着都像对的。章节名是逐字节比的，统一成一种写法。不影响等级。",
      /* `chapter:` 写坏的唯一形状：只写了竖线右边的说明，没写名字。那一行看着像
         声明了一个章节，实际一个字都不生效——这一步静静继承 parent 的章节，人却
         以为自己开了一条新线。和 bad_branch / bad_pipeline 同一条路：报一声、
         当没写、继续建树，那半句说明原样留在文件里。 */
      "lint.chapter.unnamed": "这行 `chapter:` 只写了竖线右边的说明「{note}」，没写章节名，所以它不算声明——这一步仍然继承 parent 的章节。写法是 `chapter: 消融实验 | 这一块是干什么的`。你写的那半句话原样留在文件里，一个字没动。不影响等级。",

      /* 详情面板上的三个动作。「从这里开一个章节」这句话本身就在教人标在哪一步——
         叫「设置章节」的话，读起来像每一步都该设一次。 */
      "chapter.set.act": "从这里开一个章节",
      "chapter.set.act.title": "给这一步开启的那一块起个名字。长在它下面的东西都属于同一块，所以要写这一行的只有这一步。",
      "chapter.set.prompt": "这一块叫什么？它管这一步，以及它底下所有没自己起名字的步骤：",
      "chapter.unset.act": "这里不开章节",
      "chapter.unset.act.title": "把这一行从这一步上拿掉。它回到「父步骤属于哪一块，它就属于哪一块」，底下那些自己没起过名字的步骤也跟着回去。",
      "chapter.write.act": "✎ 写清这个章节是什么",
      "chapter.write.act.title": "一句话说清这一块是干什么的。它跟着章节走，不跟着这一步走——它就写在那行章节的竖线右边。",
      "chapter.write.prompt": "这个章节是干什么的？一句话，说的是这一块，不是这一步：",

      /* 按章节导出：论文里本来就是两段，导出跟着分开。 */
      "export.chapter.head": "按章节导出",
      "export.chapter.note": "论文里主实验一段 Methods、消融另一段，导出就照这个分开。每一章各从自己的成果编一份——这是另一份派生，不是把整项目那份过滤掉几行。",
      "export.chapter.all": "整个项目",
      "export.chapter.one": "只要 {chapter}",
      "export.chapter.file": "存成 {file}",
      "export.chapter.file.title": "章节名是文件名的一部分，所以导出消融那一份，永远不会盖掉主实验的 Methods 草稿。",

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
      /* ⑦ 编辑侧只有两个输入：候选自己声明「我是候选」，分叉点自己写「在决定什么」。
         hint 要把「标成候选意味着什么」说完整——尤其是「其余的最终会被标 dead」，
         因为界面上永远不会有一个「选它」按钮：那个按钮就是双真相源。 */
      "editor.branch.label": "这一步和父步骤是什么关系",
      "editor.branch.extends": "延伸 —— 接着父步骤往下做",
      "editor.branch.alternative": "互斥候选 —— 一个问题分出来的一条路，最后只走一条",
      "editor.branch.hint": "标成候选，说的是父步骤那里提了一个问题，而这一步是它的答案之一。兄弟之间不会互相登记：每个候选只在自己身上写这个标记，把它们放在一起读才得到那一组。哪条路赢了，你就把其余几条标成 `dead`——记录这次决定的就只有这件事，再没有别的。",
      "editor.branch.note.label": "这个候选一句话 · 可不写",
      "editor.branch.note.placeholder": "对少数类重采样，而不是给损失加权",
      "editor.decision.label": "这一步开出的那个问题 · 可不写",
      "editor.decision.placeholder": "类别不平衡怎么处理？只能选一条走下去",
      "editor.decision.hint": "写在分叉的那一步上，不是写在分出去的路上。两个孩子只能证明当时有两条路；只有这一行说得出这两条路是在选什么——和「为什么」一样，它是机器替你生成不了的那种句子。",
      /* ⑧ 编辑侧只有一个三选一，而且默认那一档就是「别管它」：流程成员是算出来的，
         手工设的那两档是**事实**，会过期，所以 hint 要先劝人别用，再说清什么时候
         非用不可。 */
      "editor.pipeline.label": "这一步和定稿流程的关系",
      "editor.pipeline.auto": "算出来 —— 从成果顺着输入往回追",
      "editor.pipeline.include": "留在流程里 —— 反推追不到它，但它确实是流程的一环",
      "editor.pipeline.exclude": "不算流程 —— 它成功了，只是不属于这个方法",
      "editor.pipeline.hint": "绝大多数时候别动这里：成员是从成果反推出来的，它自己会保持正确。这两个手工挡是留给反推真的判错的那些情况——比如产物是手工拷过去的、从来没写成 `input:`，又比如一个成功了但不该让别人重跑的旁支试验。两者都声明在这一步自己身上；项目那一侧永远不存成员清单。",
      "editor.pipeline.note.label": "一句话说清为什么",
      "editor.pipeline.note.placeholder": "探索性的，成功了，但下游没有任何一步读它",
      /* 它**不是**可选的，写入侧不写就 400。上一版这里印着「可不写」，于是人选了
         exclude、跳过这一栏、按保存，收到的是服务端一句中文报错——界面亲口许了一个
         它管不着的诺。理由必填是这个键和 `branch:` 唯一的分歧，原因就写在下面。 */
      "editor.pipeline.note.required": "这一栏必须写。候选组在树上一出现就看得见，而这一行除了改变一份导出之外不留任何痕迹——没有那半句话，分不清它是想清楚的决定还是一次误点。",
      /* ⑨ 章节。这一栏最要紧的事是**别让人每一步都填**：填二十遍，改一次名字就要
         动二十个文件，而那正是继承想省掉的。所以 hint 第一句说的是「填在哪一步」，
         不是「这一栏是什么」。 */
      "editor.chapter.label": "这条线属于哪个章节",
      "editor.chapter.placeholder": "消融实验",
      "editor.chapter.hint": "只在**开启一条线**的那一步填，别的步骤一律留空：底下每一步都继承。二十步的消融只要一个文件里的一行，以后改章节名也只动那一行。在一个已经继承了章节的步骤上填，就是从这里分出一条新线——它底下的子树跟着改名。",
      "editor.chapter.inherited": "正在继承 {id} 的「{chapter}」，留空就留在这一章",
      "editor.chapter.note.label": "这个章节是什么 · 可选",
      "editor.chapter.note.placeholder": "逐个拿掉模块，对着主实验的 023 比",
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
      "search.where.fork": "当时在决定什么",
      "search.where.tr.title": "译文标题",
      "search.where.tr.body": "译文正文",
      /* 章节名和它那句说明也是人写的散文，`grep -r "消融"` 找得到，站内搜索
         就不能找不到——否则「消融那块当时是怎么想的」还得一个项目一个项目翻。 */
      "search.where.chapter": "章节名与说明",
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
      /* 三条里最重的一条，理由同上。 */
      "toast.deleted.result": "已删除 {id}，而项目笔记把它声明成了成果——定稿流程正是从它反推出来的，现在没有起点了。趁 id 还没被重用，去改掉或撤掉那一行。",
      "toast.insights.saved": "已保存（只替换了四个洞察小节）",
      "toast.insight.added": "已记入「{label}」",
      "toast.insight.updated": "已重写 {id}——id 没变，指向它的引用照样有效",
      "toast.insight.superseded": "{id} 取代了 {old}；{old} 折起来了，没有删",
      "toast.moved": "已把 {id} 挂到 {parent} 下——原来的父节点和你写的原因都进笔记了",
      "toast.moved.root": "{id} 现在自己是一棵树的根了——这次移动已记进笔记",
      "toast.moved.chapter": "章节 {from} → {to}",
      "toast.moved.chapter.also": "（跟着换章的还有 {n} 步）",
      "toast.moved.fork": "{at} 那个岔路口现在剩 {n} 个候选",
      "toast.moved.fork.roots": "森林的根之间那一组候选现在剩 {n} 个",
      "toast.branch.alternative": "{id} 现在是候选了——{parent} 下面共 {n} 条路，最后走一条",
      "toast.branch.extends": "{id} 又读作普通的延伸了",
      "toast.decision.saved": "「在决定什么」已写在 {id} 上",
      "toast.decision.cleared": "{id} 上那行决策问题已删掉；下面的候选一个没动",
      /* ⑧ 声明成果的直接后果是看不见的：流程是算出来的，界面上只会「多出一条链」。
         所以这几条 toast 要顺手说出那条链是怎么来的。 */
      "toast.result.marked": "{id} 现在是声明出来的成果了——它背后的流程从各步的输入反推出来",
      "toast.result.unmarked": "{id} 不再是声明的成果了；这一步本身一个字没变",
      "toast.pipeline.include": "{id} 被人手留在流程里——这句话写在它自己的笔记里，项目那边不存清单",
      "toast.pipeline.exclude": "{id} 不算在流程里了；开发路径上它还在原来的位置，一点没动",
      "toast.pipeline.auto": "{id} 改回按输入算了",
      "toast.export.ready": "{name} 已生成——同一份记录每次编出来逐字节一致",
      "toast.export.chapter.ready": "{chapter} · {name} 已生成——同一份记录每次编出来逐字节一致",
      /* ⑨ 标一个章节的后果是**整棵子树**跟着改归属，而屏幕上一棵子树和一个光杆
         节点长得一模一样（和 drag.carry 同一条理由）。所以带走了几步要说出来。 */
      "toast.chapter.set": "{id} 开启了「{chapter}」——它底下没自己起名字的步骤都归这一章",
      "toast.chapter.carry": "底下 {n} 步跟着一起归过去",
      "toast.chapter.cleared": "{id} 不再开启章节；父步骤属于哪一章，它就属于哪一章",
      "toast.chapter.desc.saved": "已记在「{chapter}」上——这句说明属于章节，不属于这一步",
      "toast.chapter.desc.saved.elsewhere": "已记在「{chapter}」上——这句说明住在开启这一章的 {id} 那一步，所以改动落在那个文件里",
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
      "count.alternatives": "{n} 个候选",
      "count.rejoins": "{n} 处汇回",
      "count.chapters": "{n} 个章节",
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

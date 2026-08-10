/* web/app.js 里那层纯函数的断言。跑：node --test tests/app.test.js
 * （tests/test_web.py 也会把它拉起来，所以 pytest 一样能发现它挂了。）
 *
 * app.js 的界面部分依赖 document，在 node 里跑不起来；但「洞察小节怎么切」
 * 「草稿键怎么拼」这些逻辑一旦写错，后果是静默毁记录 / 丢正文，所以它们被剥进
 * traceUtil，在这里逐条钉住。
 */
const { test } = require("node:test");
const assert = require("node:assert");

const U = require("../web/app.js");
const i18n = require("../web/i18n.js");

/* ------------------------------------------------ 洞察：不许把删除原因卷进提交 */

test("编辑洞察时，「## 已删除」不进可编辑文本 —— 它是步骤被真删之后唯一的证据", () => {
  const body = [
    "## 核心想法", "- 试试对比学习预训练", "",
    "## 已删除", "- `001` 误建的测试步骤 —— 粘错了令牌", "",
  ].join("\n");
  const r = U.splitInsightBody(body);
  assert.ok(!r.editable.includes("已删除"), r.editable);
  assert.ok(!r.editable.includes("粘错了令牌"), r.editable);
  assert.equal(r.others.length, 1);
  assert.equal(r.others[0].heading, "已删除");
  assert.ok(r.others[0].text.includes("粘错了令牌"));
});

test("四个洞察小节缺哪个补哪个，已有的内容一字不动", () => {
  const r = U.splitInsightBody("## 有效\n- 分层学习率\n");
  assert.ok(r.editable.includes("- 分层学习率"));
  U.INSIGHT_HEADINGS.forEach((h) => assert.ok(r.editable.includes("## " + h), h + " 缺了"));
  // 「有效」只能出现一次，补齐不能补出第二个同名小节（那会让洞察裂成两套）
  assert.equal((r.editable.match(/^## 有效$/gm) || []).length, 1, r.editable);
});

test("标题之前的引言留在可编辑文本里 —— 服务端合并时只保留提交文本里的引言", () => {
  const r = U.splitInsightBody("这个课题在做什么。\n\n## 坑\n- CUDA 12 上 pin_memory 会挂\n");
  assert.ok(r.editable.startsWith("这个课题在做什么。"), r.editable);
});

test("foreignHeadings 能提前说出哪几节会被服务端丢弃", () => {
  const bad = U.foreignHeadings("## 有效\n- a\n\n## 已删除\n- b\n\n## 随手记\n- c\n");
  assert.deepEqual(bad, ["已删除", "随手记"]);
  assert.deepEqual(U.foreignHeadings("## 坑\n- a\n"), []);
});

test("splitSections 和 trace_write._split_sections 对同一段正文切法一致", () => {
  const secs = U.splitSections("前言\n\n## A\na1\n### B\nb1\n");
  assert.deepEqual(secs.map((s) => s.heading), [null, "A", "B"]);
  assert.ok(secs[1].lines[0].startsWith("## A"));
});

/* ------------------------------------------------ 草稿键 */

test("草稿键把项目和步骤都编码进去 —— 不编码的话不同项目会撞同一个键", () => {
  assert.notEqual(U.draftKey("a:b", "c"), U.draftKey("a", "b:c"));
  assert.notEqual(U.draftKey("proj1", "001"), U.draftKey("proj2", "001"));
  assert.ok(U.draftKey("中文课题", "001").startsWith("trace.draft:"));
});

test("中英两份正文的草稿各存各的 —— 共用一个键就是写完中文切去写英文再回来发现被盖了", () => {
  assert.notEqual(U.draftKey("p", "007", "en"), U.draftKey("p", "007"));
  assert.notEqual(U.draftKey("p", "007", "en"), U.draftKey("p", "007", "ja"));
});

test("原文的草稿键一个字节都没变 —— 改了的话，升级前写到一半的草稿会变成找不回的孤儿", () => {
  assert.equal(U.draftKey("p", "007"), "trace.draft:p:007");
  assert.equal(U.draftKey("p", "007", ""), "trace.draft:p:007");
});

/* ------------------------------------------------ 跨项目搜索 */

test("matches 覆盖 id / 标题 / 正文 / 标签，且大小写不敏感", () => {
  const s = { id: "007", title: "对比学习预训练", body: "AGNews 上掉了 2 个点", tags: ["ABLATION"] };
  assert.ok(U.matches(s, "007"));
  assert.ok(U.matches(s, "对比学习"));
  assert.ok(U.matches(s, "agnews"));
  assert.ok(U.matches(s, "ablation"));
  assert.ok(U.matches(s, ""), "空查询不该过滤掉任何东西");
  assert.ok(!U.matches(s, "蒸馏"));
});

test("snippet 截的是命中处的上下文，不是正文开头", () => {
  const body = "x".repeat(200) + "对比学习" + "y".repeat(200);
  const out = U.snippet(body, "对比学习");
  assert.ok(out.includes("对比学习"), out);
  assert.ok(out.startsWith("…") && out.endsWith("…"), out);
  assert.ok(out.length < 120, "片段不该整段吐出来：" + out.length);
});

test("snippet 把换行压成一行 —— 结果列表是定高的一行，多行会撑破布局", () => {
  assert.equal(U.snippet("a\n\nb   c", "b"), "a b c");
});

test("干草堆里包含译文 —— 界面切成英文之后搜 contrastive 一条都搜不到就等于没做双语", () => {
  const s = { id: "007", title: "对比学习预训练", body: "AGNews 上掉了 2 个点", tags: [],
              tr: { en: { title: "Contrastive pre-training", body: "Lost 2 points on AGNews" } } };
  assert.ok(U.matches(s, "contrastive"));
  assert.ok(U.matches(s, "lost 2 points"));
  assert.ok(U.matches(s, "对比学习"), "加了译文不该把原文挤出干草堆");
});

/* ------------------------------------------------ 双语：显示哪一份、说不说明 */

test("有译文就用译文，而且不必对读者说明", () => {
  const s = { lang: "zh", tr: { en: { title: "Add title field", body: "## Why" } } };
  const p = U.pickLang(s, "en");
  assert.equal(p.tr.title, "Add title field");
  assert.equal(p.fallback, false);
  assert.equal(p.why, "");
});

test("没译文但原文就是这个语言 —— 什么都不用说，那本来就是读者要的语言", () => {
  const p = U.pickLang({ lang: "en", tr: {} }, "en");
  assert.equal(p.tr, null);
  assert.equal(p.fallback, false);
  assert.equal(p.why, "");
});

test("原文声明了别的语言 —— 可以说清那是哪一种（why=declared）", () => {
  const p = U.pickLang({ lang: "zh", tr: {} }, "en");
  assert.equal(p.fallback, true);
  assert.equal(p.why, "declared");
});

test("原文没声明 lang —— 只能说「这是原文」，绝不许猜它是哪种语言", () => {
  // 这一条是「不许猜」那条规矩的落点：正文里有汉字也好、有英文也好，
  // 没写 lang: 就是不知道，猜错了就是对读者说谎。
  const p = U.pickLang({ lang: "", body: "## 为什么\n试试", tr: {} }, "en");
  assert.equal(p.fallback, true);
  assert.equal(p.why, "unknown", "推断出 zh 就是在猜");
});

test("小节名认语言只查那张封闭词表，不做语种识别", () => {
  const T = { zh: i18n.tIn("zh", "template.body"), en: i18n.tIn("en", "template.body") };
  assert.equal(U.langByHeadings("## 为什么\n因为\n## 结论\n成立\n", T), "zh");
  assert.equal(U.langByHeadings("## Why\nbecause\n", T), "en");
  // 满篇汉字但一个已知小节名都没有 → 认不出来，返回空串让调用方自己兜底
  assert.equal(U.langByHeadings("今天跑了三个种子，结果都一样。", T), "");
  assert.equal(U.langByHeadings("", T), "");
});

test("模板里的五个小节名和 i18n 的表逐字一致（i18n 那侧又对着 trace_core.SECTION_NAMES）", () => {
  const zh = Object.keys(U.headingsIn(i18n.tIn("zh", "template.body")));
  const en = Object.keys(U.headingsIn(i18n.tIn("en", "template.body")));
  assert.equal(zh.length, 5);
  assert.equal(en.length, 5);
  assert.ok(zh.includes("为什么") && en.includes("Why"));
});

/* ------------------------------------------------ 等级表 */

test("L0–L4 五级齐全且和 FORMAT.md 第 10 节同序", () => {
  assert.deepEqual(U.LEVELS, ["L0", "L1", "L2", "L3", "L4"]);
  // 标签和判据说明搬进了 i18n（这一层没有界面语言，不该替界面决定说哪种话），
  // 但「每一级都得有话说」这条不变，两种语言都得有。
  ["en", "zh"].forEach((l) => {
    U.LEVELS.forEach((lv) => {
      assert.ok(i18n.tIn(l, "trace.level." + lv) !== "trace.level." + lv, l + " 缺 " + lv + " 的标签");
      assert.ok(i18n.tIn(l, "trace.level." + lv + ".hint") !== "trace.level." + lv + ".hint",
                l + " 缺 " + lv + " 的判据说明");
    });
  });
  assert.ok(U.levelIndex("L0") < U.levelIndex("L4"));
  assert.equal(U.levelIndex("乱写的"), 0, "认不出来的等级按最低算，不能当成最高");
});

test("四种 repro 状态两种语言都有标签，failed 也必须有 —— 失败记录是结论不是错误", () => {
  assert.deepEqual(U.REPRO_STATES.slice().sort(), ["failed", "runnable", "unknown", "verified"]);
  ["en", "zh"].forEach((l) => {
    U.REPRO_STATES.forEach((s) => {
      assert.ok(i18n.tIn(l, "trace.repro." + s) !== "trace.repro." + s, l + " 缺 " + s + " 的标签");
    });
  });
});

/* ------------------------------------------------ ③ 结构化路径的回写 */

test("回写 path 时 role 和属性一个都不许掉 —— 改个标题就抹掉刚核对完的校验和是最贵的一种 bug", () => {
  const p = { location: "/orange/lab/pockets", role: "output", note: "纯 RNA 口袋",
              attrs: { n: "4554", size: "620756992", md5: "7d4e1a9c" } };
  assert.equal(U.formatPath(p),
    "/orange/lab/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992 md5=7d4e1a9c");
});

test("老写法「位置 | 说明」回写之后逐字不变 —— 向后兼容是硬要求", () => {
  assert.equal(U.formatPath({ location: "/blue/x", note: "去重后的训练集，12 GB", role: "", attrs: {} }),
               "/blue/x | 去重后的训练集，12 GB");
  assert.equal(U.formatPath({ location: "/blue/x", note: "", role: "", attrs: {} }), "/blue/x");
});

test("词表之外的 role 落回说明位 —— 写入侧只认四个词，硬塞进 role 位会被拒", () => {
  assert.equal(U.formatPath({ location: "/x", role: "sideways", note: "", attrs: {} }), "/x");
});

test("format_code 跳过空位置的尾段 —— `git | | commit=…` 那种行写不进去", () => {
  assert.equal(U.formatCode({ kind: "snapshot", location: "/orange/snap", note: "",
                              attrs: { manifest: "MANIFEST.md5", n: "43" } }),
               "snapshot | /orange/snap | manifest=MANIFEST.md5 n=43");
  assert.equal(U.formatCode({ kind: "git", location: "", note: "", attrs: {} }), "git");
});

test("format_input 没写说明时不留一个孤零零的竖线", () => {
  assert.equal(U.formatInput({ step: "013", note: "pocket_composition.csv" }), "013 | pocket_composition.csv");
  assert.equal(U.formatInput({ step: "013", note: "" }), "013");
});

test("字节数分到 GB / TB —— 57 GB 的目录只到 MB 会显示成 58366 MB，那个数没人读得出来", () => {
  assert.equal(U.sizeUnit(61203283968).key, "unit.gb");
  assert.equal(U.sizeUnit(61203283968).n, "57.0");
  assert.equal(U.sizeUnit(620756992).key, "unit.mb");
  assert.equal(U.sizeUnit(3 * 1099511627776).key, "unit.tb");
  assert.equal(U.sizeUnit(900).key, "unit.b");
  assert.equal(U.sizeUnit(null), null, "没写 size 的路径不该显示成 0 B");
});

/* ------------------------------------------------ ④ 洞察的 id 与取代 */

test("洞察行读得出 id、正文和「取代了谁」，三样分得干净", () => {
  const got = U.parseInsightLine("`p3` PDBFixer 误杀 944 个带修饰残基，见 [[013b]] · 取代 p1");
  assert.equal(got.id, "p3");
  assert.equal(got.text, "PDBFixer 误杀 944 个带修饰残基，见 [[013b]]");
  assert.deepEqual(got.supersedes, ["p1"]);
});

test("英文那一侧用 supersedes，认的是同一张封闭词表", () => {
  assert.deepEqual(U.parseInsightLine("`p3` PDBFixer over-deletes · supersedes p1").supersedes, ["p1"]);
});

test("「p1 已被取代」是派生的 —— 只有取代者身上写着那半句，被取代的那条一个字都没改", () => {
  const body = ["## 坑",
                "- `p3` PDBFixer 误杀 944 个 · 取代 p1",
                "- `p1` PDBFixer 误杀 1,099 个"].join("\n");
  const got = U.parseInsights(body).pitfall;
  assert.equal(got.length, 2);
  assert.deepEqual(got[1].superseded_by, ["p3"]);
  assert.deepEqual(got[0].superseded_by, [], "取代者自己没有被取代");
  assert.equal(got[1].raw, "`p1` PDBFixer 误杀 1,099 个", "被取代那一行的原文一个字都不许动");
});

test("没有 id 的旧洞察照常读得出来 —— 现存数据全是这样的", () => {
  const got = U.parseInsights("## 无效\n- 回译一直没用\n").fails;
  assert.equal(got.length, 1);
  assert.equal(got[0].id, "");
  assert.equal(got[0].text, "回译一直没用");
});

test("洞察小节的子标题不结束本节，别的同级标题才结束 —— 和 trace_core.sections() 同一套层级语义", () => {
  const body = ["## 坑", "### 数据", "- `p1` a", "## 已删除", "- `002` 误建"].join("\n");
  const got = U.parseInsights(body);
  assert.equal(got.pitfall.length, 1, "子标题下面那条也算「坑」");
  assert.ok(!JSON.stringify(got).includes("误建"), "「已删除」里的行绝不能被当成洞察");
});

test("英文小节名同样认得 —— 界面切成英文之后洞察不该退化成一段死文本", () => {
  assert.equal(U.parseInsights("## Doesn't work\n- `p1` back-translation never helped\n").fails.length, 1);
});

/* ------------------------------------------------ ① 移动的当场校验 */

const MOVE_TREE = {
  "001": { id: "001", parent: "" },
  "002": { id: "002", parent: "001" },
  "003": { id: "003", parent: "002" },
  "010": { id: "010", parent: "" },
};

test("挂到自己的后代下面当场拒 —— 这不是笔误，是想法本身有问题，不能等服务端 400", () => {
  assert.equal(U.moveError(MOVE_TREE, "001", "003"), "descendant");
  assert.equal(U.moveError(MOVE_TREE, "001", "001"), "self");
});

test("挂到不相干的另一棵树上是允许的 —— 移动的全部意义就是「当时归错了地方」", () => {
  assert.equal(U.moveError(MOVE_TREE, "003", "010"), "");
  assert.equal(U.moveError(MOVE_TREE, "003", ""), "", "提为根也算一次移动");
});

test("原地不动不是一次移动 —— 那会往文件里追加一条什么都没说的审计", () => {
  assert.equal(U.moveError(MOVE_TREE, "002", "001"), "noop");
  assert.equal(U.moveError(MOVE_TREE, "001", ""), "noop");
});

test("目标不存在时说得出来，而不是让人点了确定才知道", () => {
  assert.equal(U.moveError(MOVE_TREE, "002", "999"), "missing");
});

/* ------------------------------------------------ ①b 拖拽里那些不是 DOM 的判断
 *
 * 手势本身测不了（node 里没有指针），但拖拽里真正会把记录写坏的三件事都不是 DOM：
 * 多远才算「我要拖」、指针底下压着的是哪一张卡、这一拖带走了哪几步。
 */

test("起拖要过阈值 —— 没有它，每一次选中节点都可能变成一次带永久审计的移动", () => {
  assert.equal(U.beyondSlop(0, 0), false);
  assert.equal(U.beyondSlop(3, 3), false, "手抖三四个像素不算拖");
  assert.equal(U.beyondSlop(5, 0), true);
  assert.equal(U.beyondSlop(0, -5), true, "反方向同样算");
  assert.equal(U.beyondSlop(-4, -4), true, "斜着走的是直线距离，不是某一根轴");
  assert.ok(U.DRAG_SLOP > 0);
  // 阈值可以由调用方指定，好让触屏那种更粗的手指有自己的一档
  assert.equal(U.beyondSlop(6, 0, 12), false);
});

test("拖一步就是拖它整棵子树 —— 后端本来就是这个语义，屏幕上必须看得见那一片", () => {
  assert.deepEqual(U.subtreeIds(MOVE_TREE, "001").sort(), ["001", "002", "003"]);
  assert.deepEqual(U.subtreeIds(MOVE_TREE, "003"), ["003"], "叶子只带自己");
  assert.deepEqual(U.subtreeIds(MOVE_TREE, "010"), ["010"]);
  assert.equal(U.subtreeIds(MOVE_TREE, "001")[0], "001", "自己排在最前面");
  assert.deepEqual(U.subtreeIds(MOVE_TREE, "999"), [], "不存在的步骤带不走任何东西");
});

test("子树是沿 parent 反查出来的，不读 children —— children 是服务端派生的第二份真相", () => {
  // 故意给一份 children 和 parent 说法相反的数据：只有 parent 那一份算数
  const lying = {
    a: { id: "a", parent: "", children: ["zzz"] },
    b: { id: "b", parent: "a", children: [] },
  };
  assert.deepEqual(U.subtreeIds(lying, "a").sort(), ["a", "b"]);
});

test("父指针成环时子树不转死 —— 磁盘上真出现过环的话，界面也得能画出来", () => {
  const ring = { x: { id: "x", parent: "y" }, y: { id: "y", parent: "x" } };
  assert.deepEqual(U.subtreeIds(ring, "x").sort(), ["x", "y"]);
});

test("命中测试认坐标不认 DOM —— 拖动时指针底下悬着的是跟手的标签", () => {
  const rects = [
    { id: "001", x: 0, y: 0, w: 100, h: 40 },
    { id: "002", x: 120, y: 0, w: 100, h: 40 },
  ];
  assert.equal(U.hitRect(rects, 10, 10), "001");
  assert.equal(U.hitRect(rects, 150, 39), "002");
  assert.equal(U.hitRect(rects, 110, 10), "", "两张卡之间的空隙不是任何一张卡");
  assert.equal(U.hitRect(rects, 10, 40), "", "下边界是开区间，不然相邻两行会同时命中");
  assert.equal(U.hitRect([], 0, 0), "");
  assert.equal(U.hitRect(undefined, 0, 0), "");
});

test("重叠时后画的那张赢 —— 屏幕上盖在上面的就是人以为自己指着的那张", () => {
  const rects = [
    { id: "under", x: 0, y: 0, w: 100, h: 40 },
    { id: "over", x: 50, y: 0, w: 100, h: 40 },
  ];
  assert.equal(U.hitRect(rects, 60, 10), "over");
});

test("列表命中越界回 -1 而不是 0 —— 「拖到列表下方的空白」不能变成「拖到第一行」", () => {
  assert.equal(U.rowAt(0, 28, 3), 0);
  assert.equal(U.rowAt(27.9, 28, 3), 0);
  assert.equal(U.rowAt(28, 28, 3), 1);
  assert.equal(U.rowAt(83, 28, 3), 2);
  assert.equal(U.rowAt(84, 28, 3), -1, "最后一行下面是空白，不是最后一行");
  assert.equal(U.rowAt(-1, 28, 3), -1, "列表上方同理");
  assert.equal(U.rowAt(10, 0, 3), -1, "行高还没算出来时不许瞎猜一行");
});

test("拖拽的合法性判断没有第二份 —— 落点问的就是对话框问的那个 moveError", () => {
  // 这条钉的是「不许再写一套」：两套判断迟早不一致，而不一致的那一刻
  // 用户看到的是「能拖，拖完报错」。所以 traceUtil 里只该有这一个判官。
  const names = Object.keys(U).filter((k) => /^(drop|canDrop|dragError|dropError)/.test(k));
  assert.deepEqual(names, [], "又出现了第二个落点判官：" + names.join(", "));
  assert.equal(typeof U.moveError, "function");
});

/* ------------------------------------------------ ⑥ 提示级不和真警告混在一起 */

test("三条新诊断是提示级 —— 它们不影响 L0–L4，混进警告栏人就不再看警告栏了", () => {
  ["section_without_prose", "table_without_explanation", "code_without_explanation"].forEach((c) => {
    assert.equal(U.warnLevel({ level: "warn", code: c }), "hint", c);
  });
  /* 分叉那几条同样一格等级都不降。 */
  ["lone_alternative", "fork_without_decision",
   "decision_without_candidates"].forEach((c) => {
    assert.equal(U.warnLevel({ level: "warn", code: c }), "hint", c);
  });
  /* undecided_fork 连提示栏都不进，它是**待办**，由页面上那条 #forkbar 专门说。
     以前它两处都说：警告栏里逐条一遍，forkbar 里又汇总一遍。同一件事说两遍会让
     人以为自己犯了错，而人消除「错误」最省事的办法是随手把一条支标成 dead——
     那是拿假结论换一屏干净的输出。trace_cli.py 的 TODO_CODES 是同一条判断。 */
  assert.equal(U.warnLevel({ level: "warn", code: "undecided_fork" }), "todo");
  /* bad_branch 反过来：它**不是**提示。`branch: alterative` 落盘之后既不算候选、
     也不在页面上留下任何痕迹，而人以为自己标过了 —— 那是「你写下的东西正在被
     忽略」，和 bad_status 同一档，属于真警告。 */
  assert.equal(U.warnLevel({ level: "warn", code: "bad_branch" }), "warn");
  assert.equal(U.warnLevel({ level: "warn", code: "dangling_input" }), "warn");
  assert.equal(U.warnLevel({ level: "error", code: "bad_frontmatter" }), "error");
});

/* ------------------------------------------------ ② 数据流布局 */

/* 016 的输入同时来自 013 和 014 —— 树上只能表达一个，这正是要画第二张图的理由。 */
const FLOW_STEPS = [
  { id: "013", parent: "", inputs: [] },
  { id: "014", parent: "013", inputs: [] },
  { id: "013b", parent: "013", inputs: [] },
  { id: "016", parent: "013b", inputs: [{ step: "013" }, { step: "014" }] },
];

test("层号沿依赖走：一步的层永远深于它的每一个依赖，于是每条边都朝下", () => {
  const L = U.flowLayout(FLOW_STEPS);
  L.edges.forEach((e) => {
    assert.ok(L.nodes[e.to].layer > L.nodes[e.from].layer,
              `${e.from} → ${e.to} 这条边没朝下走`);
  });
  assert.equal(L.nodes["016"].layer, 2, "016 要落在 014 之下，而不是只按 parent 算");
});

test("parent 和 input 同时指向同一步时是一条 both 边 —— 它占绝大多数边，混进 data 会让人以为树边画丢了", () => {
  const L = U.flowLayout([{ id: "001", parent: "", inputs: [] },
                          { id: "002", parent: "001", inputs: [{ step: "001" }] }]);
  assert.equal(L.edges.length, 1);
  assert.equal(L.edges[0].kind, "both");
});

test("只有 parent 的边是 tree，只有 input 的边是 data", () => {
  const L = U.flowLayout(FLOW_STEPS);
  const kind = {};
  L.edges.forEach((e) => { kind[e.from + ">" + e.to] = e.kind; });
  assert.equal(kind["013b>016"], "tree");
  assert.equal(kind["014>016"], "data");
  assert.equal(kind["013>016"], "data", "013 既是数据源又是祖先，但树上它不是 016 的父");
});

test("布局是纯函数：同一份数据永远得到同一张图 —— 形状本身是信息，这就是不做力导向的理由", () => {
  assert.deepEqual(U.flowLayout(FLOW_STEPS), U.flowLayout(FLOW_STEPS));
});

test("悬空和自指的 input 不进图，也不让布局死掉 —— 删掉一步之后引用变悬空是已接受的代价", () => {
  const L = U.flowLayout([{ id: "001", parent: "", inputs: [{ step: "404" }, { step: "001" }] }]);
  assert.equal(L.edges.length, 0);
  assert.equal(L.nodes["001"].layer, 0);
});

test("数据依赖成环时不死循环 —— 环由服务端报警告，图还得画得出来", () => {
  const L = U.flowLayout([{ id: "001", parent: "", inputs: [{ step: "002" }] },
                          { id: "002", parent: "", inputs: [{ step: "001" }] }]);
  assert.equal(Object.keys(L.nodes).length, 2);
  assert.equal(L.edges.length, 2);
});

test("空项目不产出负数尺寸的画布", () => {
  const L = U.flowLayout([]);
  assert.equal(L.w, 0);
  assert.equal(L.h, 0);
});

test("上游闭包沿 parent ∪ inputs 走 —— 在一张 DAG 上「祖先」只有沿依赖走才有意义", () => {
  const byId = {};
  FLOW_STEPS.forEach((s) => { byId[s.id] = s; });
  const got = U.depClosure(byId, "016");
  assert.deepEqual(Object.keys(got).sort(), ["013", "013b", "014", "016"]);
  assert.deepEqual(Object.keys(U.depClosure(byId, "014")).sort(), ["013", "014"]);
});

/* ------------------------------------------------ 载入行为 */

test("在没有 document 的环境里 require app.js 不会启动界面", () => {
  // 上面所有 require 都已经跑过一遍了；能走到这里就说明界面那个 IIFE 提前返回了。
  assert.equal(typeof U.splitInsightBody, "function");
  assert.equal(typeof globalThis.document, "undefined");
});

/* ------------------------------------------------ 继承路径：抄位置，不抄结论 */

test("从父步骤继承路径时，核对结论和度量一个都不跟着抄", () => {
  const p = {
    location: "/blue/lab/cif", role: "input", note: "原始 CIF",
    attrs: { n: "4554", size: "61203283968", md5: "7d4e1a9c",
             checked: "2026-08-09", nodes: "12" },
  };
  const got = U.inheritPath(p);
  assert.equal(U.formatPath(got), "/blue/lab/cif | input | 原始 CIF | nodes=12");
  assert.deepEqual(got.attrs, { nodes: "12" },
    "size/n/md5/checked 是「有人真去看过一眼」的度量和结论，" +
    "抄进一个还没跑过的步骤就是伪造证据；认不出的 nodes= 留着，替人删字更糟");
});

test("一个今天才建出来的步骤，不许一出生就声称那份数据没了", () => {
  const got = U.inheritPath({
    location: "/orange/ckpt", role: "output", note: "权重",
    attrs: { missing: "2026-08-09", size: "277872640" },
  });
  assert.equal(U.formatPath(got), "/orange/ckpt | output | 权重");
});

test("位置、角色、说明恰恰应该继承 —— 同一条线上数据在哪多半没变", () => {
  const got = U.inheritPath({ location: "/x", role: "script", note: "跑这一步的脚本", attrs: {} });
  assert.equal(U.formatPath(got), "/x | script | 跑这一步的脚本");
});

/* ------------------------------------------------ 搜索：位置也得搜得到 */

test("产物和代码的位置进搜索干草堆，校验和与日期不进", () => {
  const step = {
    id: "001", title: "训练", body: "## 为什么\n因为\n", tags: [],
    paths: [{ location: "/orange/lab/ckpt/run042/best.pt", note: "权重",
              attrs: { md5: "7d4e1a9c", checked: "2026-08-09" } }],
    code: [{ kind: "snapshot", location: "/orange/snap/20260809", note: "" }],
    inputs: [{ step: "001", note: "pocket_composition.csv" }],
  };
  assert.ok(U.matches(step, "best.pt"), "grep -rn best.pt 一秒答得出，站内搜索不该更弱");
  assert.ok(U.matches(step, "20260809"));
  assert.ok(U.matches(step, "pocket_composition"));
  assert.ok(!U.matches(step, "7d4e1a9c"),
    "搜一串 md5 是核对不是找东西，把它拼进干草堆只会制造噪声命中");
});

/* --------------------------------- 拖拽：落点必须在屏幕上看得见 */

test("指针不在可视区域上时，命中一律作废", () => {
  // 画布/列表的矩形在滚动时会伸到视口外去，所以光换算坐标是不够的：
  // 指针停在顶栏的搜索框上、或者右边的详情面板上，照样能换算出画布里某个
  // **屏幕上根本看不见**的节点。松手就是一次挂到看不见的地方的移动——
  // 而移动会写一条永久的审计记录。这条测试钉的就是那道闸门。
  const view = { left: 0, top: 240, right: 600, bottom: 760 };
  assert.ok(U.withinRect(view, 300, 500), "画布正中当然算");
  assert.ok(!U.withinRect(view, 300, 20), "顶栏的搜索框在画布上方");
  assert.ok(!U.withinRect(view, 900, 500), "详情面板在画布右边");
  assert.ok(!U.withinRect(view, 300, 830), "图例下方也不算");
  assert.ok(!U.withinRect(null, 300, 500), "元素还没渲染时不许当成命中");
});

test("可视区域的边界算在里面", () => {
  // 贴着边松手是很常见的动作，差一个像素就"什么都没发生"会让人以为拖坏了。
  const view = { left: 10, top: 10, right: 100, bottom: 100 };
  for (const [x, y] of [[10, 10], [100, 100], [10, 100], [100, 10]])
    assert.ok(U.withinRect(view, x, y), `角点 ${x},${y} 应当算命中`);
  assert.ok(!U.withinRect(view, 101, 50));
});

/* ============================================ ⑦ 三种关系的几何与判据
 *
 * 这一整块钉的是「非颜色的那半个通道真的存在」。颜色是这次改动的主角，但规格
 * 只放宽了一半：每一种关系必须再配一个形状。几何算错了，屏幕上只是「有点歪」，
 * 没人会去查——而歪掉的正是灰度打印和色觉障碍下唯一还剩的那条信息。
 */

const NODES = {
  "012": { x: 0, y: 200 },      // 三个候选，同一层
  "012b": { x: 200, y: 200 },
  "012c": { x: 400, y: 200 },
  "011": { x: 200, y: 100 },    // 分叉点
};
const GROUP = { at: "011", decision: "怎么办", options: ["012", "012b", "012c"],
                live: ["012b"], state: "decided", chosen: "012b" };

test("括弧横跨整组候选：两端落在最左和最右那个候选的中线上", () => {
  const bk = U.forkBracket(NODES, GROUP, { nw: 100 });
  assert.equal(bk.x1, 50, "左端要压在 012 的中线上");
  assert.equal(bk.x2, 450, "右端要压在 012c 的中线上");
  assert.equal(bk.cx, 250);
  assert.ok(bk.y < 200, "括弧必须在候选**上方**，不能压在卡片上");
});

test("只有一个候选时括弧照画 —— lone_alternative 说的正是「一个候选不成其为选择」", () => {
  // 宽度收成 0 的话，那条诊断在图上就完全看不见，人只会以为自己标错了地方。
  const bk = U.forkBracket(NODES, { at: "011", options: ["012b"], live: [], state: "abandoned" },
                           { nw: 100 });
  assert.ok(bk.x2 - bk.x1 >= 30, "退化成一个点了：" + (bk.x2 - bk.x1));
  assert.equal(bk.cx, 250, "还是要居中在那一个候选上");
});

test("括弧和它的标注全部挤在层与层之间那道缝里 —— 溢出去就被父节点的卡片压住", () => {
  // V_GAP 是 38（trace_core），卡片高 NODE_H。lift 一大，标注就顶进父卡片里，
  // 而 #dmarks 排在 #dnodes 之前 —— 被压住就等于这句话在图上根本不存在。
  const V_GAP = 38, NH = 58;
  const nodes = { p: { x: 0, y: 0 }, a: { x: 0, y: NH + V_GAP }, b: { x: 180, y: NH + V_GAP } };
  const bk = U.forkBracket(nodes, { at: "p", options: ["a", "b"] }, { nw: 176 });
  const parentBottom = NH;
  assert.ok(bk.y > parentBottom + 3, "括弧顶进父卡片了");
  assert.ok(bk.y - 17 > parentBottom, "标注（括弧上方 ~17px）顶进父卡片了");
  assert.equal(bk.side, false, "有父节点的组不该走「标注挪到右边」那一档");
});

test("根之间那一组会说「标注没地方摆在上面」—— 它上面没有节点，摆上去就被裁掉", () => {
  const roots = { a: { x: 0, y: 24 }, b: { x: 200, y: 24 } };   // PAD = 24
  const bk = U.forkBracket(roots, { at: "", options: ["a", "b"] }, { nw: 176 });
  assert.equal(bk.side, true);
});

test("布局里没有这一组的坐标时返回 null，不返回一条画在原点的括弧", () => {
  assert.equal(U.forkBracket({}, GROUP, { nw: 100 }), null);
  assert.equal(U.forkBracket(NODES, { at: "x", options: [] }, {}), null);
  assert.equal(U.forkBracket(NODES, null, {}), null);
});

test("三态各说各的话，而「都不行」是结论不是错误", () => {
  assert.equal(U.forkLabel(GROUP).key, "decision.settled");
  assert.equal(U.forkLabel(GROUP).vars.id, "012b", "「已定」要指名是哪一条活下来了");

  const dead = { options: ["a", "b"], live: [], state: "abandoned" };
  assert.equal(U.forkLabel(dead).key, "decision.alldead");
  // P4：全废是这个问题的答案（「都不行」），不是一个待填的窟窿。
  // 文案 key 里出现 warn / error / missing 就说明它被当成缺陷了。
  assert.ok(!/warn|error|missing|lint/.test(U.forkLabel(dead).key + U.forkLabel(dead).title));

  const open = { options: ["a", "b", "c"], live: ["a", "b"], state: "open" };
  assert.equal(U.forkLabel(open).key, "decision.pick");
  assert.equal(U.forkLabel(open).vars.n, 2, "「N 选 1」的 N 是还活着的条数，不是候选总数");
  assert.equal(U.forkLabel(null), null);
});

test("汇回是曲线，树边是正交折线 —— 形状本身就说明它不属于树", () => {
  // 这是汇回那半个非颜色通道。改成折线的话，灰度打印下它和父子边一模一样，
  // 读者只会把它读成「第二个 parent」，而那正是 P1 最不想让人误会的事。
  const cv = U.rejoinCurve({ x: 0, y: 0 }, { x: 400, y: 300 }, { nw: 100, nh: 50 });
  assert.ok(cv.d.includes("C"), "不是三次贝塞尔：" + cv.d);
  assert.ok(!/[VH]/.test(cv.d), "混进了正交段：" + cv.d);
  assert.ok(cv.arrow.endsWith("Z"), "箭头不是闭合三角形：" + cv.arrow);
});

test("汇回从卡片的侧边进出，不抢上下边 —— 上下边是父子边的地盘", () => {
  const cv = U.rejoinCurve({ x: 0, y: 0 }, { x: 400, y: 300 }, { nw: 100, nh: 50 });
  assert.ok(cv.d.startsWith("M100 25"), "该从生产者的右边出去：" + cv.d);
  assert.ok(cv.d.endsWith("400 325"), "该扎进消费者的左边：" + cv.d);
});

test("消费者在左边时整条曲线镜像过来，箭头跟着掉头", () => {
  const cv = U.rejoinCurve({ x: 400, y: 0 }, { x: 0, y: 300 }, { nw: 100, nh: 50 });
  assert.ok(cv.d.startsWith("M400 25"), "该从生产者的左边出去：" + cv.d);
  assert.ok(cv.d.endsWith("100 325"), "该扎进消费者的右边：" + cv.d);
  // 箭头的底边要在尖端的**右侧**（指向左），否则画出来是一个倒着的箭头
  const back = Number(/^M([-\d.]+)/.exec(cv.arrow)[1]);
  assert.ok(back > 100, "箭头掉头失败：" + cv.arrow);
});

test("生产者在消费者下面（更深的一层）照样画得出来 —— 汇回不看行序", () => {
  // 011 分叉出 012/012b，012b 底下的 013 汇回 012 底下的 014 时，
  // 生产者的行号反而比消费者大。几何这一层要是偷偷假设「从上往下」，
  // 最典型的那种汇回就画反了。
  const cv = U.rejoinCurve({ x: 0, y: 400 }, { x: 300, y: 100 }, { nw: 100, nh: 50 });
  assert.ok(cv.d.includes("C"));
  assert.ok(cv.d.endsWith("300 125"), cv.d);
});

test("轨道图上的汇回从右边那条空档绕过去，箭头朝左扎回节点", () => {
  // git graph 里横过来并进主线的那条线就是这个位置。从轨道中间穿的话，
  // 它会和一根根竖着的轨道线缠在一起，谁也看不出哪条是哪条。
  const cv = U.railRejoin({ x: 12, y: 50 }, { x: 26, y: 400 }, 90);
  assert.ok(cv.d.includes("C90 50 90 400"), "控制点没落在留出来的空档上：" + cv.d);
  assert.ok(cv.d.includes("C"), cv.d);
  const tip = Number(/L[-\d.]+ [-\d.]+L([-\d.]+)/.exec(cv.arrow)[1]);
  const back = Number(/^M([-\d.]+)/.exec(cv.arrow)[1]);
  assert.ok(back > tip, "箭头得朝左指回节点：" + cv.arrow);
});

test("汇回边的淡出判据不能套「两端都在祖先链上」—— 那样它永远是淡的", () => {
  // 汇回按定义两端分属两条支线，绝不可能同时在一条祖先链上。套那条判据的话，
  // 一选中任何节点，所有汇回边集体消失 = 这个功能在选中状态下不存在。
  const m = { from: "013", to: "014", at: "011" };
  const chain = { "014": 1, "011": 1, "001": 1 };     // 选中 014 时的祖先链
  assert.ok(U.rejoinRelated(m, "014", chain), "选中消费者时这条边必须亮着");
  assert.ok(U.rejoinRelated(m, "013", { "013": 1, "012b": 1 }), "选中生产者时也一样");
  assert.ok(!U.rejoinRelated(m, "099", { "099": 1 }), "跟选中毫无关系时才淡出");
  assert.ok(U.rejoinRelated(m, "", {}), "没有选中时一条都不淡");
});

test("「我是哪一组的候选」是现算的，不看任何存下来的归属字段", () => {
  const groups = [
    { at: "", options: ["001", "001b"] },
    { at: "011", options: ["012", "012b"] },
  ];
  assert.equal(U.groupOf(groups, { id: "012", parent: "011", branch: "alternative" }).at, "011");
  // 根之间那一组：parent 为空，对应 at === ""
  assert.equal(U.groupOf(groups, { id: "001b", parent: "", branch: "alternative" }).at, "");
  // 没标 branch 的普通延伸不属于任何一组，哪怕它的兄弟是候选
  assert.equal(U.groupOf(groups, { id: "013", parent: "011", branch: "extends" }), null);
  assert.equal(U.groupOf(groups, { id: "012", parent: "011" }), null);
});

test("「互斥候选」这个取值只有一个字面量 —— 和 trace_core.BRANCH_KINDS 对得上", () => {
  // core 那一侧写的是 ("extends", "alternative")。这边多一个字母，整张图上的
  // 候选就一条都认不出来，而页面不会报任何错。
  assert.equal(U.BRANCH_ALT, "alternative");
});

/* ------------------------------------------------ ⑦ 分叉那两句散文进搜索干草堆 */

test("`decision:` 和候选说明搜得到 —— grep 一秒答得出的事，站内搜索不能答不出", () => {
  const s = { id: "011", title: "基线", body: "跑了一遍。", tags: [],
              decision: "类别不平衡怎么处理？只能选一条走下去" };
  const c = { id: "012", title: "只调采样权重", body: "", tags: [],
              branch: "alternative", branch_note: "先试最便宜的" };
  assert.ok(U.matches(s, "类别不平衡"), "搜不到 decision —— 那是唯一只能人写的一句话");
  assert.ok(U.matches(c, "最便宜"), "搜不到候选自己那句说明");
  // 取值不进干草堆：进了的话搜 "alternative" 会命中半棵树
  assert.equal(U.forkHay(c).indexOf("alternative"), -1);
  assert.equal(U.forkHay({ id: "013", decision: "", branch_note: "" }), "");
});

test("干草堆只加东西不改判据 —— 没有 decision 的一步，搜出来的结果和从前一样", () => {
  const s = { id: "013", title: "产出分数", body: "写了 scores.csv", tags: ["数据"] };
  assert.ok(U.matches(s, "scores"));
  assert.ok(U.matches(s, "数据"));
  assert.ok(!U.matches(s, "类别不平衡"));
});

/* -------------------- ⑦ 验收时在浏览器里抓到的三处几何/语义错 */

test("只有一个候选的分叉不许被说成「已定」", () => {
  // core 的 state 只数**还活着**的候选，1 个活的就叫 decided —— 对 2 选 1 是对的，
  // 对「从头到尾只有 1 条」是在替人宣布一件没发生的事。
  // CLI、MCP、以及同一个页面顶上的提示栏说的都是「只有一个候选」，
  // 只有这块牌子说「已定」，页面自己跟自己打架。
  const lone = U.forkLabel({ at: "013", state: "decided", options: ["014"], live: ["014"], chosen: "014" });
  assert.equal(lone.state, "lone");
  const real = U.forkLabel({ at: "005", state: "decided", options: ["006", "007"], live: ["006"], chosen: "006" });
  assert.equal(real.state, "decided", "两个候选里定下一个，那才是真的已定");
});

test("括弧在不属于这一组的兄弟头上开一个真的口子", () => {
  // 候选和普通延伸挂在同一个父节点下、普通那条按 id 序正好夹在中间时，
  // 一道连续的横杠会圈住三张卡片而牌子写「2 选 1」——圈住的和数出来的对不上，
  // 而人相信的是自己看见的那一圈。
  const nodes = { a: { x: 0, y: 200 }, m: { x: 200, y: 200 }, b: { x: 400, y: 200 } };
  const g = { at: "p", options: ["a", "b"] };
  const withOut = U.forkBracket(nodes, g, { nw: 176, skip: [288] });   // m 的中心
  const solid = U.forkBracket(nodes, g, { nw: 176 });
  const subpaths = d => (d.match(/M/g) || []).length;
  assert.ok(subpaths(withOut.d) > subpaths(solid.d), "横杠必须真的断开，不是画两道竖挡了事");
  assert.ok(!/H\s*-?\d/.test(""), "占位");
});

test("同一层的汇回不许退化成一条直线", () => {
  // 控制点只往水平方向伸时，两端同 y 会让四个点的 y 全相等——画出来是笔直的横线，
  // 和普通的短正交边一模一样。而「生产者和消费者在同一层」恰恰是最常见的汇回形状，
  // 灰度下曲线是唯一还能分辨它的东西。
  const flat = U.curveBetween({ x: 0, y: 100 }, { x: 120, y: 100 }, 24);
  const ys = (flat.d.match(/-?\d+(\.\d+)?/g) || []).filter((_, i) => i % 2 === 1).map(Number);
  assert.ok(Math.max(...ys) - Math.min(...ys) > 6, "同层时必须拱起来，否则形状通道当场消失");
  const slope = U.curveBetween({ x: 0, y: 100 }, { x: 120, y: 260 }, 24);
  assert.ok(/C/.test(slope.d), "不同层时仍然是曲线");
});

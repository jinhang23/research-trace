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

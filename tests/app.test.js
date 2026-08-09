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

/* ------------------------------------------------ 载入行为 */

test("在没有 document 的环境里 require app.js 不会启动界面", () => {
  // 上面所有 require 都已经跑过一遍了；能走到这里就说明界面那个 IIFE 提前返回了。
  assert.equal(typeof U.splitInsightBody, "function");
  assert.equal(typeof globalThis.document, "undefined");
});

/* web/i18n.js 的断言。跑：node --test tests/i18n.test.js
 *
 * 这一层最容易坏的方式不是某一句翻得不好，而是**加了中文忘了英文**：
 * 页面上突然冒出一个 key 名，或者更糟——悄悄回退成中文，于是漏翻的地方永远
 * 没人发现。所以本文件里最要紧的一条是「两种语言的 key 集合完全相同」，
 * 其余的按危害排序：注入（文案会被插进 DOM）、占位符丢失、内容模板被界面语言
 * 带跑偏（那会让 note.md 的小节名和 trace_core.SECTION_NAMES 对不上）。
 */
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const i18n = require("../web/i18n.js");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "web", "i18n.js"), "utf8");

/* 每条测试自带一份干净的 localStorage：语言偏好是全局状态，测试之间互相污染
   会让失败信息指向错误的地方。 */
function withStore(seed) {
  const box = Object.assign(Object.create(null), seed || {});
  globalThis.localStorage = {
    getItem(k) { return k in box ? box[k] : null; },
    setItem(k, v) { box[k] = String(v); },
    removeItem(k) { delete box[k]; },
  };
  return box;
}
function noStore() { delete globalThis.localStorage; }

function values(lang) {
  return Object.entries(i18n.STRINGS[lang]).flatMap(([k, v]) =>
    typeof v === "string" ? [[k, v]] : Object.values(v).map((s) => [k, s]));
}
function placeholders(s) {
  return [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
}

/* ------------------------------------------------ 最重要的一条：两边一样全 */

test("en 和 zh 的 key 集合完全相同 —— 防「加了中文忘了英文」", () => {
  const en = Object.keys(i18n.STRINGS.en).sort();
  const zh = Object.keys(i18n.STRINGS.zh).sort();
  const onlyEn = en.filter((k) => !zh.includes(k));
  const onlyZh = zh.filter((k) => !en.includes(k));
  assert.deepEqual(onlyEn, [], "只有英文有：" + onlyEn.join(", "));
  assert.deepEqual(onlyZh, [], "只有中文有：" + onlyZh.join(", "));
  assert.ok(en.length > 200, "文案表只有 " + en.length + " 条，界面上不可能只有这么点字");
});

test("同一个 key 在两种语言里的占位符集合一致 —— 翻译时最常见的事故是把 {n} 弄丢", () => {
  for (const k of Object.keys(i18n.STRINGS.en)) {
    const pe = placeholders(pickAny(i18n.STRINGS.en[k]));
    const pz = placeholders(pickAny(i18n.STRINGS.zh[k]));
    assert.deepEqual(pz, pe, `${k}: en 用 {${pe}}，zh 用 {${pz}}`);
  }
  function pickAny(v) { return typeof v === "string" ? v : v.other; }
});

test("复数形式只能是 {one, other}，且 other 必须在 —— other 是兜底的那一支", () => {
  for (const lang of ["en", "zh"]) {
    for (const [k, v] of Object.entries(i18n.STRINGS[lang])) {
      if (typeof v === "string") continue;
      assert.deepEqual(Object.keys(v).sort(), ["one", "other"], `${lang}.${k} 的复数形式不对`);
    }
  }
});

/* ------------------------------------------------ 语言选择 */

test("首次访问是英文：localStorage 里什么都没有时 lang() 回 DEFAULT", () => {
  withStore();
  assert.equal(i18n.DEFAULT, "en");
  assert.equal(i18n.lang(), "en");
  noStore();
});

test("localStorage 里是垃圾值时回退到默认语言，不是崩也不是空白界面", () => {
  withStore({ "trace.lang": "克林贡语" });
  assert.equal(i18n.lang(), "en");
  noStore();
});

test("setLang 写进 localStorage 的 trace.lang，并在 window 上派发 tracelang", () => {
  const box = withStore();
  const seen = [];
  globalThis.dispatchEvent = (e) => { seen.push(e); return true; };

  assert.equal(i18n.setLang("zh"), "zh");
  assert.equal(box["trace.lang"], "zh");
  assert.equal(i18n.lang(), "zh");
  assert.equal(seen.length, 1);
  assert.equal(seen[0].type, "tracelang");
  assert.equal(seen[0].detail.lang, "zh", "接线方靠 detail.lang 决定重绘成哪一种");

  // 不认识的语言不生效：否则界面会切成一整屏 key 名
  assert.equal(i18n.setLang("tlh"), "zh");
  assert.equal(seen.length, 1, "无效语言不该派发事件，接线方会白重绘一次");

  delete globalThis.dispatchEvent;
  noStore();
});

test("locale() 跟着界面语言走 —— 英文界面里的日期不该排成 2026/8/9", () => {
  withStore({ "trace.lang": "zh" });
  assert.equal(i18n.locale(), "zh-CN");
  i18n.setLang("en");
  assert.equal(i18n.locale(), "en-US");
  noStore();
});

/* ------------------------------------------------ 取词与插值 */

test("缺 key 时返回 key 本身而不是崩，并且 warn（同一个 key 只吵一次）", () => {
  withStore();
  const real = console.warn;
  const said = [];
  console.warn = (m) => said.push(m);
  try {
    assert.equal(i18n.t("nope.not.a.key"), "nope.not.a.key");
    assert.equal(i18n.t("nope.not.a.key"), "nope.not.a.key");
    assert.equal(said.length, 1, "同一个缺失 key 吵两遍会淹掉别的告警：" + said.join(" / "));
    assert.ok(said[0].includes("nope.not.a.key"));
  } finally { console.warn = real; noStore(); }
});

test("插值按 {name} 替换；缺变量时占位符原样留着并 warn —— 留个显眼的洞好过悄悄变空", () => {
  withStore({ "trace.lang": "en" });
  assert.equal(i18n.t("toast.created", { id: "007" }), "Created 007");
  const real = console.warn;
  console.warn = () => {};
  try {
    assert.ok(i18n.t("toast.created").includes("{id}"));
  } finally { console.warn = real; noStore(); }
});

test("复数按 vars.n 选：英文 1 和 3 不同，中文不受影响", () => {
  withStore({ "trace.lang": "en" });
  assert.equal(i18n.t("count.steps", { n: 1 }), "1 step");
  assert.equal(i18n.t("count.steps", { n: 3 }), "3 steps");
  assert.equal(i18n.t("count.steps", { n: 0 }), "0 steps");
  i18n.setLang("zh");
  assert.equal(i18n.t("count.steps", { n: 1 }), "1 步");
  assert.equal(i18n.t("count.steps", { n: 3 }), "3 步");
  noStore();
});

/* ------------------------------------------------ 转义：文案会被插进 DOM */

test("t() 返回纯文本、不转义 —— 目的地是 textContent / title / prompt，再转一遍就会看见 &quot;", () => {
  withStore({ "trace.lang": "en" });
  const out = i18n.t("confirm.delete.title", { id: "007", title: 'a "quoted" <b> title' });
  assert.ok(out.includes('a "quoted" <b> title'), out);
  assert.ok(!out.includes("&quot;"), "t() 不该转义：" + out);
  noStore();
});

test("tHtml() 转义变量 —— 步骤标题、搜索词、服务端错误原文都会经过这里", () => {
  withStore({ "trace.lang": "en" });
  const evil = '<img src=x onerror="alert(1)">';
  const out = i18n.tHtml("confirm.delete.title", { id: "007", title: evil });
  assert.ok(!out.includes("<img"), "变量没被转义，这就是一个注入点：" + out);
  assert.ok(out.includes("&lt;img"), out);
  noStore();
});

test("tHtml() 只展开 **粗体** · `代码` · 换行三种标记，且变量里的标记不算数", () => {
  withStore({ "trace.lang": "en" });
  assert.ok(i18n.tHtml("home.empty").includes("<b>＋ Project</b>"));
  assert.ok(i18n.tHtml("insight.lead").includes("<code>project.md</code>"));
  assert.ok(i18n.tHtml("home.nofilter", { q: "x" }).includes("<br>"), "换行要变成 <br>");
  // 步骤标题里出现 ** 很常见（指标表里加粗最好的一行），它不该把界面变成粗体
  const out = i18n.tHtml("toast.created", { id: "**007**" });
  assert.ok(!out.includes("<b>"), "变量里的 ** 被当成标记了：" + out);
  noStore();
});

test("要放已经拼好的 HTML 必须显式写成 {html: …} —— 别让「这里是 HTML」靠猜", () => {
  withStore({ "trace.lang": "en" });
  const out = i18n.tHtml("trace.weakest", {
    link: { html: '<a href="#003">003</a>' }, title: "<script>",
  });
  assert.ok(out.includes('<a href="#003">003</a>'));
  assert.ok(out.includes("&lt;script&gt;"), "没标 html 的变量仍然要转义：" + out);
  noStore();
});

test("文案表里一个 HTML 标签都没有 —— 表本身永远不可能是注入源", () => {
  for (const lang of ["en", "zh"]) {
    for (const [k, v] of values(lang)) {
      assert.ok(!/<[a-zA-Z/!]/.test(v), `${lang}.${k} 里有 HTML 标签：${v}`);
    }
  }
});

/* ------------------------------------------------ 内容模板跟内容语言走 */

test("正文模板不跟界面语言：界面英文时依然能拿到中文模板", () => {
  withStore({ "trace.lang": "en" });
  assert.ok(i18n.tIn("zh", "template.body").startsWith("## 为什么"));
  assert.ok(i18n.tIn("en", "template.body").startsWith("## Why"));
  assert.notEqual(i18n.tIn("zh", "template.body"), i18n.t("template.body"));
  noStore();
});

test("模板的五个小节和 trace_core.SECTION_NAMES 逐字一致 —— 差一个字评级就找不到内容", () => {
  const core = fs.readFileSync(path.join(ROOT, "trace_core.py"), "utf8");
  const block = /SECTION_NAMES\s*=\s*\{([\s\S]*?)\n\}/.exec(core);
  if (!block) {
    // 另一个 agent 还没落地 SECTION_NAMES 时跳过，而不是把整条流水线弄红
    console.log("  (trace_core.py 里还没有 SECTION_NAMES，跳过)");
    return;
  }
  const order = ["why", "what", "result", "conclusion", "next"];
  for (const lang of ["zh", "en"]) {
    const tpl = i18n.tIn(lang, "template.body");
    let at = -1;
    for (const slot of order) {
      const re = new RegExp(`"${slot}"\\s*:\\s*\\{[^}]*"${lang}"\\s*:\\s*"([^"]+)"`);
      const m = re.exec(block[1]);
      assert.ok(m, `SECTION_NAMES 里没有 ${slot}.${lang}`);
      const idx = tpl.indexOf("## " + m[1]);
      assert.ok(idx >= 0, `${lang} 模板里缺小节「${m[1]}」：\n${tpl}`);
      assert.ok(idx > at, `${lang} 模板里「${m[1]}」的位置和 SECTION_NAMES 的顺序对不上`);
      at = idx;
    }
  }
});

test("洞察的四个标签和 trace_core.INSIGHT_NAMES 逐字一致 —— 按钮说的和落进 project.md 的必须是同一个词", () => {
  const core = fs.readFileSync(path.join(ROOT, "trace_core.py"), "utf8");
  const block = /INSIGHT_NAMES\s*=\s*\{([\s\S]*?)\n\}/.exec(core);
  if (!block) {
    console.log("  (trace_core.py 里还没有 INSIGHT_NAMES，跳过)");
    return;
  }
  for (const kind of ["idea", "works", "fails", "pitfall"]) {
    for (const lang of ["zh", "en"]) {
      const re = new RegExp(`"${kind}"\\s*:\\s*\\{[^}]*"${lang}"\\s*:\\s*"([^"]+)"`);
      const m = re.exec(block[1]);
      assert.ok(m, `INSIGHT_NAMES 里没有 ${kind}.${lang}`);
      assert.equal(i18n.tIn(lang, "insight." + kind), m[1],
        `insight.${kind} 的 ${lang} 标签和 INSIGHT_NAMES 对不上`);
    }
  }
});

/* ------------------------------------------------ 服务端算出来的中文条目 */

test("trace_core 算出的 missing 条目在英文界面上不会漏出中文", () => {
  const core = fs.readFileSync(path.join(ROOT, "trace_core.py"), "utf8");
  const items = [...core.matchAll(/missing\.append\("([^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(items.length >= 6, "trace_core 里的 missing 条目只找到 " + items.length + " 条");

  withStore({ "trace.lang": "en" });
  const seen = new Set();
  for (const zh of items) {
    const out = i18n.traceMissing(zh);
    assert.notEqual(out, zh, `这一条没被认出来，英文界面上会直接漏中文：${zh}`);
    assert.ok(!/[一-鿿]/.test(out), `英文里混进中文了：${out}`);
    assert.ok(!seen.has(out), `两条 missing 撞到同一条译文了：${zh} → ${out}`);
    seen.add(out);
  }
  noStore();
});

test("认不出来的 missing 条目原样显示 —— 老实给中文，好过悄悄吞掉一条待办", () => {
  withStore({ "trace.lang": "en" });
  assert.equal(i18n.traceMissing("以后新加的某一条"), "以后新加的某一条");
  assert.equal(i18n.traceMissing(""), "");
  noStore();
});

/* ------------------------------------------------ 载入方式 */

test("零依赖、能被 node 单独 require，且不依赖 md.js —— 载入顺序不该决定页面白不白屏", () => {
  assert.ok(!/\brequire\s*\(/.test(SRC), "i18n.js 里出现了 require");
  assert.ok(!/^\s*import\s/m.test(SRC), "i18n.js 里出现了 import");
  assert.ok(!/window\.md\b/.test(SRC), "i18n.js 依赖了 md.js");
  assert.equal(typeof i18n.t, "function");
  assert.equal(typeof globalThis.document, "undefined");
});

test("契约里承诺的那几个名字都在", () => {
  for (const k of ["DEFAULT", "lang", "setLang", "t", "STRINGS"]) {
    assert.ok(k in i18n, "window.i18n 少了 " + k);
  }
  assert.ok(i18n.STRINGS.en && i18n.STRINGS.zh);
});

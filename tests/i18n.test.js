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
/* 复数值取 other 那一支当代表：断言「这条文案说了什么」时，单复数不影响措辞。 */
function one(lang, key) {
  const v = i18n.STRINGS[lang][key];
  return typeof v === "string" ? v : v.other;
}
/* 上面那条「en/zh key 集合完全相同」只发现得了「加了一边忘了另一边」，
   发现不了「整组都忘了加」——那种情况下两边一样地缺，测试全绿，而接线的人
   在页面上拿到一屏 key 名。所以每一组新文案都要在这里点名。 */
function needKeys(keys, why) {
  for (const k of keys) {
    for (const lang of ["en", "zh"]) {
      assert.ok(k in i18n.STRINGS[lang], `${lang} 少了 ${k} —— ${why}`);
      assert.ok(one(lang, k).trim().length > 0, `${lang}.${k} 是空的`);
    }
  }
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

/* ------------------------------------------------ 六条改动各自的文案
 *
 * 这一节按「网页上会怎么呈现」组织，不按 key 前缀组织：每一组问的是
 * “接线的人要画这块界面，手里的字够不够”。缺一个 key 的后果不是少一句话，
 * 是那块界面上出现一个点分的 key 名。
 */

test("① 移动审计：展示行、徽章、对话框、四类报错都有文案", () => {
  needKeys([
    "move.act", "move.act.title", "move.badge", "move.badge.title",
    "move.head", "move.entry", "move.entry.nobody",
    "move.from.root", "move.to.root", "move.by.human",
    "move.dialog.title", "move.dialog.hint",
    "move.field.parent", "move.parent.none", "move.parent.current",
    "move.field.reason", "move.reason.placeholder", "move.reason.required",
    "move.submit", "move.cancel",
    "move.err.reason", "move.err.self", "move.err.cycle",
    "move.err.descendant", "move.err.noop", "move.err.project", "move.err.missing",
    "count.moves", "toast.moved", "toast.moved.root",
  ], "移动记录要能展示、能发起、能被拒绝，缺一环这个功能就只有一半");

  // 「2026-08-09 从 014 移到 013b —— 原因（谁）」这一行要能整句拼出来
  assert.deepEqual(placeholders(one("en", "move.entry")),
    ["by", "date", "from", "reason", "to"].sort());
});

test("① 「原因必填」的文案要说清为什么必填，不是只写「必填」", () => {
  // 复述规则（“这一项是必填的”）不解释任何事；这套文案的性格是解释「为什么」。
  assert.match(one("zh", "move.reason.required"), /必填/);
  assert.match(one("en", "move.reason.required"), /Required/);
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "move.reason.required").length > 40,
      `${lang}.move.reason.required 太短了，说不出「为什么必填」`);
  }
});

test("① editor.hint 不再说 parent 不可改 —— P2 重新定义之后那句话是错的", () => {
  // 旧文案：「`id` 和 `parent` 不可改」。parent 现在可改（带审计），
  // 界面上留着旧承诺会让人不敢用移动功能，或者用了之后以为记录坏了。
  assert.match(one("zh", "editor.hint"), /可以移动/);
  assert.match(one("en", "editor.hint"), /can be moved/);
  assert.ok(!/和\s*`?parent`?\s*不可改/.test(one("zh", "editor.hint")),
    "editor.hint 还在说 parent 不可改");
  // id 那一半仍然成立，不能一起松掉
  assert.match(one("zh", "editor.hint"), /`id` 不可改/);
  assert.match(one("en", "editor.hint"), /`id` never changes/);
});

test("①b 拖拽：跟手的标签、落区、两种拖不动、取消，都有文案", () => {
  needKeys([
    "drag.aim.parent", "drag.aim.root", "drag.aim.none",
    "drag.no.self", "drag.no.descendant", "drag.no.noop", "drag.no.missing",
    "drag.carry", "drag.root.label", "drag.root.hint",
    "drag.cancelled", "drag.readonly", "drag.flow", "move.dragged.note",
  ], "手势不是自解释的：落到哪、为什么落不下、为什么拖不动，都得有话说");
});

test("①b 跟手的标签必须短 —— 读它的人正在拖东西，一眼一句", () => {
  for (const lang of ["en", "zh"]) {
    for (const k of ["drag.aim.parent", "drag.aim.root", "drag.no.self",
                     "drag.no.descendant", "drag.no.noop", "drag.no.missing"]) {
      assert.ok(one(lang, k).length <= 40, `${lang}.${k} 太长，贴着指针读不完：${one(lang, k)}`);
    }
  }
});

test("①b 「拖不动」要说为什么，不是只说不行", () => {
  // 只读那一条要点出「这是静态导出」，否则人只会以为功能坏了
  assert.match(one("en", "drag.readonly"), /static export/);
  assert.match(one("zh", "drag.readonly"), /静态导出/);
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "drag.readonly").length > 40, `${lang}.drag.readonly 说不出为什么`);
    assert.ok(one(lang, "drag.flow").length > 40, `${lang}.drag.flow 说不出为什么`);
  }
});

test("①b 最要紧的那一句：拖拽只改 parent，inputs 一个字没动", () => {
  // 手势最容易造成的误会就是「我把数据流也接过去了」。两种语言都必须挡住它。
  for (const lang of ["en", "zh"]) {
    assert.match(one(lang, "move.dragged.note"), /parent/);
    assert.match(one(lang, "move.dragged.note"), /inputs/);
    assert.match(one(lang, "drag.flow"), /parent/);
    assert.match(one(lang, "drag.flow"), /inputs/);
  }
});

test("①b 提为根的落区要说清「只有这里算」—— 不然它就是最容易误触发的那个判定", () => {
  assert.match(one("en", "drag.root.hint"), /Only here/i);
  assert.match(one("zh", "drag.root.hint"), /只有这里/);
});

test("②b 拖走一片时要说走的是几步 —— 拖一棵子树和拖一个光杆节点在屏幕上一模一样", () => {
  assert.deepEqual(placeholders(one("en", "drag.carry")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "drag.carry")), ["n"]);
});

test("② 数据依赖：两个方向的清单、数据流视图、三类警告都有文案", () => {
  needKeys([
    "input.lead", "input.parent.tip",
    "input.head", "input.empty", "input.entry", "input.entry.bare",
    "input.consumers.head", "input.consumers.empty",
    "input.warn.missing", "input.warn.self", "input.warn.cycle",
    "app.view.flow", "app.view.flow.title",
    "flow.title", "flow.lead",
    "flow.legend.tree", "flow.legend.data", "flow.legend.both", "flow.empty",
    "editor.inputs.label", "editor.inputs.placeholder", "editor.inputs.hint",
    "newstep.field.inputs", "detail.meta.inputs",
    "count.inputs", "count.consumers",
  ], "数据流是 DAG、树是单父，两个方向的清单都要能画出来");
});

test("② 最要紧的那一句：parent 和 input 是两件事，两种语言都要说出来", () => {
  // 这一句不写，读者只会把 input 当成「第二个 parent」，然后开始怀疑树错了。
  const en = one("en", "input.lead"), zh = one("zh", "input.lead");
  for (const s of [en, zh]) {
    assert.match(s, /parent/);
    assert.match(s, /input/);
  }
  assert.match(en, /bytes/, "英文里要点明 input 说的是字节从哪来");
  assert.match(zh, /字节/, "中文里要点明 input 说的是字节从哪来");
});

test("③ 结构化路径：四个 role、大小条目数校验和、三种核对状态都有文案", () => {
  needKeys([
    "path.role.input", "path.role.script", "path.role.output", "path.role.evidence",
    "path.role.input.title", "path.role.script.title",
    "path.role.output.title", "path.role.evidence.title", "path.role.none",
    "path.n", "path.checksum", "path.checksum.title",
    "path.checked", "path.checked.title",
    "path.missing", "path.missing.badge", "path.missing.title",
    "path.unchecked", "path.unchecked.title", "path.summary.missing",
    "path.attr.unknown.title",
    "editor.paths.hint",
  ], "结构化 path 的每一段都得有个说法，否则机器读出来了人还是看不懂");

  for (const lang of ["en", "zh"]) {
    const roles = ["input", "script", "output", "evidence"]
      .map((r) => one(lang, "path.role." + r));
    assert.equal(new Set(roles).size, 4, `${lang} 的四个 role 名字撞了：${roles.join(" / ")}`);
  }
});

test("③ 57 GB 的目录要显示成 57 GB —— 只有 MB 的话那个数没人读得出来", () => {
  needKeys(["unit.gb", "unit.tb"], "size= 是字节数，最大的那条是 57 GB");
  for (const lang of ["en", "zh"]) {
    assert.match(one(lang, "unit.gb"), /\{n\}/);
    assert.match(one(lang, "unit.tb"), /\{n\}/);
  }
});

test("③ 「已不存在」要显眼但不惊悚：不说错误、不说失败，并写明这一行不删", () => {
  // 目录没了是一条**发现**（P4：失败是一等公民），不是这条记录出了错。
  // 用红色警告词会让人第一反应去「修」它——而唯一能“修”的方法就是删掉那一行。
  for (const lang of ["en", "zh"]) {
    const badge = one(lang, "path.missing.badge");
    assert.ok(!/error|fail|错误|失败|警告|invalid/i.test(badge),
      `${lang}.path.missing.badge 用了报错口吻：${badge}`);
  }
  assert.match(one("zh", "path.missing.title"), /不删/);
  assert.match(one("en", "path.missing.title"), /stays/);
});

test("④ 洞察：id、取代双向、折叠展开、编辑都有文案", () => {
  needKeys([
    "insight.id", "insight.id.title",
    "insight.superseded", "insight.supersedes", "insight.badge.superseded",
    "insight.superseded.title", "insight.superseded.show", "insight.superseded.hide",
    "insight.item.edit", "insight.item.edit.prompt",
    "insight.supersede.act", "insight.supersede.prompt", "insight.supersede.hint",
    "insight.warn.missing",
    "toast.insight.updated", "toast.insight.superseded",
  ], "取代关系只写在取代者身上，被取代那一侧是派生的——但界面上两侧都要说得出来");
});

test("④ 「被取代」是折叠不是删除 —— 文案必须说清旧的那条还在", () => {
  assert.match(one("zh", "insight.superseded.title"), /留着/);
  assert.match(one("en", "insight.superseded.title"), /Kept/);
  // 「编辑」和「取代」是两件事，界面要给出选哪个的依据
  assert.ok(one("zh", "insight.supersede.hint").includes("取代")
         && one("zh", "insight.supersede.hint").includes("编辑"),
    "insight.supersede.hint 没说清什么时候用哪个");
});

test("⑤ code：三种 kind、manifest、文件数都有文案", () => {
  needKeys([
    "code.head", "code.kind.git", "code.kind.snapshot", "code.kind.container",
    "code.kind.git.title", "code.kind.snapshot.title", "code.kind.container.title",
    "code.manifest", "code.manifest.title", "code.files", "code.empty",
    "code.from.commit", "code.from.commit.title", "code.l2.note",
    "editor.code.label", "editor.code.placeholder", "editor.code.hint",
    "newstep.field.code", "detail.meta.code",
  ], "代码不在 git 里的时候也要能上 L2，界面上就得先说得出「不在 git 里」是什么样");
});

test("⑤ L2 的说法要跟着放宽：不能只提 commit，快照也算", () => {
  // 判据放宽了而文案还写着「记了 commit」，用户会以为自己那条 L2 是判错的。
  assert.match(one("en", "trace.level.L2.hint"), /snapshot/);
  assert.match(one("zh", "trace.level.L2.hint"), /快照/);
  assert.match(one("en", "trace.missing.commit"), /snapshot/);
  assert.match(one("zh", "trace.missing.commit"), /快照/);
  // 「不比 commit 差」那句话是这条改动的全部理由，要真的写出来
  for (const lang of ["en", "zh"]) assert.match(one(lang, "code.l2.note"), /commit/);
});

test("⑤ 服务端把 missing 从「commit」改写成「代码位置」时，界面上仍是同一句话", () => {
  // MISSING_MATCH 认的是 trace_core 里的中文原文。⑤ 之后那句话的措辞可能变，
  // 两种写法必须落到同一条译文——否则英文界面上会突然漏出中文（认不出就原样显示）。
  withStore({ "trace.lang": "en" });
  const a = i18n.traceMissing("没记 commit——找不回当时的代码");
  const b = i18n.traceMissing("没记代码位置——commit、快照、镜像 digest 有一个就行");
  assert.equal(a, b, "两种措辞认到了不同的译文");
  assert.ok(!/[一-鿿]/.test(a), "英文界面上漏出中文：" + a);
  noStore();
});

test("⑥ 新诊断有文案，并且写明「只是提示，不影响等级」", () => {
  needKeys([
    "lint.head", "lint.badge", "lint.note",
    "lint.level.error", "lint.level.warn", "lint.level.hint",
    "lint.subheads", "lint.table.nodesc", "lint.pre.nodesc",
  ], "提示级诊断如果看起来和降级警告一样，人会去「修」一件根本没坏的事");

  assert.match(one("zh", "lint.note"), /不影响等级/);
  assert.match(one("en", "lint.note"), /change the level/);
  // 「哪一节」得说得出来，否则提示指不到地方
  assert.deepEqual(placeholders(one("en", "lint.subheads")), ["section"]);
  assert.deepEqual(placeholders(one("zh", "lint.subheads")), ["section"]);
});

/* ------------------------------------------------ ⑦ 决策分叉 · 互斥候选 · 汇回
 *
 * 这一组文案挡的是同一件事：树上的边从此有三种含义，而**含义是看不见的**——
 * 一条边画成什么样，全靠图例把它翻译回人话。所以这里的断言比别处更硬：
 * 名字要准（不许和 git 的「分支/合并」撞车）、每种关系都要有一句说明、
 * 非颜色的那条冗余通道不许被后来的人顺手删掉、未决的措辞不许变成责备。
 */

test("⑦ 图例：三种关系各自的名字与说明都在 —— 图例是这块改动唯一的解释入口", () => {
  needKeys([
    "list.legend.extends", "list.legend.alternative", "list.legend.rejoin",
    "list.legend.extends.title", "list.legend.alternative.title", "list.legend.rejoin.title",
    "list.legend.note",
    "branch.kind.extends", "branch.kind.alternative",
    "branch.kind.extends.title", "branch.kind.alternative.title",
    "branch.badge", "branch.badge.title",
    "rejoin.kind", "rejoin.kind.title",
  ], "第一次看到彩色的边，人只能从图例知道那是什么意思；缺一条就等于那种边没解释");

  // 「这是蓝色的线」不解释任何事。每条图例都要说清它**意味着**什么。
  for (const lang of ["en", "zh"]) {
    for (const k of ["list.legend.extends", "list.legend.alternative", "list.legend.rejoin"]) {
      assert.ok(one(lang, k).length > 12, `${lang}.${k} 只有个名字，没说它意味着什么`);
    }
    for (const k of ["list.legend.alternative.title", "list.legend.rejoin.title"]) {
      assert.ok(one(lang, k).length > 40, `${lang}.${k} 太短，说不出这条边为什么长这样`);
    }
  }
});

test("⑦ 关系的名字不许和 git 撞车 —— 「分支 / 主线 / 合并」在读者脑子里已经有别的意思", () => {
  // 这套树记的是「当时接着哪一步想」，不是版本控制。名字一旦借用 git 的词，
  // 读者会自动套上 git 的模型（可以合并、可以 rebase、主线是唯一的），全是错的。
  const named = ["list.legend.extends", "list.legend.alternative", "list.legend.rejoin",
                 "branch.kind.extends", "branch.kind.alternative", "rejoin.kind",
                 "branch.badge"];
  for (const k of named) {
    assert.ok(!/\bbranch(es|ing|ed)?\b|\bmerge[ds]?\b|\bmaster\b|\bmainline\b|\btrunk\b/i.test(one("en", k)),
      `en.${k} 用了 git 的词：${one("en", k)}`);
    assert.ok(!/分支|主线|合并|主干/.test(one("zh", k)),
      `zh.${k} 用了 git 的词：${one("zh", k)}`);
  }
});

test("⑦ 图例里那条「颜色之外还有形状」不许丢 —— 打印成灰的、看不见颜色的人靠它", () => {
  // 规格放宽了「颜色不单独承载信息」，代价是必须写明冗余通道在哪。
  // 这句话一旦被后来的人当成啰嗦删掉，无障碍那一半就只剩口头承诺。
  assert.match(one("en", "list.legend.note"), /curve/i);
  assert.match(one("en", "list.legend.note"), /bracket|brace/i);
  assert.match(one("zh", "list.legend.note"), /曲线/);
  assert.match(one("zh", "list.legend.note"), /括弧|括号/);
});

test("⑦ 分叉点：N 选 1 / 已定 / 都不行 / 还没定，四种状态都有文案", () => {
  needKeys([
    "decision.pick", "decision.pick.title",
    "decision.settled", "decision.settled.title",
    "decision.alldead", "decision.alldead.title",
    "decision.open", "decision.open.title", "decision.open.note",
    "decision.open.summary", "decision.open.summary.title",
    "decision.question.label", "decision.question.title", "decision.question.missing",
  ], "一组候选有四种可能的样子，少一种那个分叉点就只能显示 key 名");

  // 「N 选 1」要说得出 N，「已定：012b」要说得出是哪一个
  assert.deepEqual(placeholders(one("en", "decision.pick")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "decision.pick")), ["n"]);
  assert.deepEqual(placeholders(one("en", "decision.settled")), ["id"]);
  assert.deepEqual(placeholders(one("zh", "decision.settled")), ["id"]);
});

test("⑦ 「未决」不许写成责备 —— 同时开几条线是研究的常态，不是没做完的作业", () => {
  // 这是这个功能真正的收益：把「我还有几个岔路口没定」摆出来。措辞一旦带上
  // 「漏了 / 应该 / 忘了」的味道，人会为了让界面干净而随手标 dead——
  // 那是拿一条假结论换一块绿色。
  for (const k of ["decision.open", "decision.open.title", "decision.open.note",
                   "decision.open.summary", "lint.fork.open"]) {
    assert.ok(!/\bshould have\b|\bforgot|\bfail(ed|ure)?\b|\berror\b|\bmust\b|\bmissing\b/i.test(one("en", k)),
      `en.${k} 像在责备用户：${one("en", k)}`);
    assert.ok(!/错误|失败|忘了|忘记|应该|必须|警告|遗漏/.test(one("zh", k)),
      `zh.${k} 像在责备用户：${one("zh", k)}`);
  }
  // 光是不责备还不够，得正面说一句「这是正常的」
  assert.match(one("en", "lint.fork.open"), /nothing is wrong|normal|how the work/i);
  assert.match(one("zh", "lint.fork.open"), /不是错|正常|常态/);
});

test("⑦ 「选了哪个」是派生的：文案要说清它是从其余几条 dead 读出来的", () => {
  // 界面上永远不会有「标记赢家」那个按钮——那个按钮就是双真相源。
  // 不把这件事说出来，人会一直找它，找不到就以为功能缺了一半。
  for (const lang of ["en", "zh"]) {
    for (const k of ["decision.settled.title", "decision.lead", "editor.branch.hint"]) {
      assert.match(one(lang, k), /dead/i, `${lang}.${k} 没说清「已定」是从 dead 推出来的`);
    }
  }
});

test("⑦ 详情面板：既要能说「我是某个决策的候选」，也要能说「我就是那个决策点」", () => {
  needKeys([
    "decision.head", "decision.lead",
    "decision.candidate.entry", "decision.candidate.entry.bare",
    "decision.candidate.dead", "decision.candidate.live",
    "decision.of.head", "decision.of.lead",
    "decision.siblings.head", "decision.siblings.lone",
    "decision.roots.head", "decision.roots.note",
    "count.alternatives",
  ], "一条边有两个端点，两边的面板都要说得出这条边是什么");

  assert.deepEqual(placeholders(one("en", "decision.of.head")), ["parent"]);
  assert.deepEqual(placeholders(one("zh", "decision.of.head")), ["parent"]);
});

test("⑦ 根之间也能成一组：得说清这一组没有地方写「在决定什么」", () => {
  // 两种互斥的开局没有共同的父节点，`decision:` 无处可挂。不说，人会去找那个
  // 输入框，找不到就以为自己漏填了——而这里根本没有能填的地方。
  assert.match(one("en", "decision.roots.note"), /root/i);
  assert.match(one("zh", "decision.roots.note"), /根/);
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "decision.roots.note").length > 40,
      `${lang}.decision.roots.note 说不出为什么这里写不了那句问题`);
  }
});

test("⑦ 汇回不是新字段：文案两边都要点明它就是 input", () => {
  needKeys([
    "rejoin.lead", "rejoin.out.head", "rejoin.in.head",
    "rejoin.entry", "rejoin.entry.bare", "rejoin.at",
    "rejoin.badge", "rejoin.badge.title",
    "count.rejoins",
  ], "汇回在详情面板里是两个方向的清单：本步的产物汇到哪去了、哪些支线汇进了本步");

  // 「在哪儿分开的」是 core 算好一起发过来的（merges[].at）。丢了它，一条汇回
  // 就只剩「有条曲线」，读者拼不出「从哪岔开、又从哪回来」这个形状。
  assert.deepEqual(placeholders(one("en", "rejoin.at")), ["id"]);
  assert.deepEqual(placeholders(one("zh", "rejoin.at")), ["id"]);

  // 说成「第三种父子关系」的话，人会去找一个记录它的字段，然后自己造一个出来——
  // 那正是双真相源。它是 input，数据早就有了，缺的只是把它画出来。
  for (const lang of ["en", "zh"]) {
    assert.match(one(lang, "rejoin.lead"), /input/);
    assert.match(one(lang, "rejoin.kind.title"), /input/);
  }
  assert.deepEqual(placeholders(one("en", "rejoin.out.head")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "rejoin.in.head")), ["n"]);
});

test("⑦ 编辑：标成候选 / 取消 / 写决策问题，三个动作和它们的解释都在", () => {
  needKeys([
    "editor.branch.label", "editor.branch.extends", "editor.branch.alternative",
    "editor.branch.hint", "editor.branch.note.label", "editor.branch.note.placeholder",
    "editor.decision.label", "editor.decision.placeholder", "editor.decision.hint",
    "decision.mark.act", "decision.mark.act.title",
    "decision.unmark.act", "decision.unmark.act.title",
    "decision.write.act", "decision.write.act.title", "decision.write.prompt",
    "toast.branch.alternative", "toast.branch.extends",
    "toast.decision.saved", "toast.decision.cleared",
  ], "写得进去这个功能才成立；只能看不能标的话，页面上永远只有一种边");

  // 「标成候选意味着什么」要讲全：一组只能选一个，其余最终会被标 dead
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "editor.branch.hint").length > 60,
      `${lang}.editor.branch.hint 太短，说不清标成候选之后会发生什么`);
  }
  // 决策问题是人写的那一句，hint 要点出它推导不出来
  assert.match(one("en", "editor.decision.hint"), /nothing can generate|cannot|can't/i);
  assert.match(one("zh", "editor.decision.hint"), /生成不了|推导不出|说不出/);
});

test("⑦ 三条新诊断：各自说清为什么值得一提，并且都写明不影响等级", () => {
  needKeys([
    "lint.alt.lone", "lint.fork.noquestion", "lint.fork.open",
  ], "只有一个候选 / 分叉没写 decision / 未决的分叉——三条都是提示级");

  for (const lang of ["en", "zh"]) {
    for (const k of ["lint.alt.lone", "lint.fork.noquestion", "lint.fork.open"]) {
      assert.ok(one(lang, k).length > 50, `${lang}.${k} 太短，说不出为什么值得一提`);
    }
    // 顶栏那句总说明（lint.note）会被当成背景板略过，所以每条自己也要说一遍：
    // 这三条最容易被误读成「我这里判低了」。
    assert.match(one("en", "lint.alt.lone"), /level/i);
    assert.match(one("en", "lint.fork.noquestion"), /level/i);
    assert.match(one("en", "lint.fork.open"), /level/i);
    assert.match(one("zh", "lint.alt.lone"), /不影响等级/);
    assert.match(one("zh", "lint.fork.noquestion"), /不影响等级/);
    assert.match(one("zh", "lint.fork.open"), /不影响等级/);
  }
  // warnText 一条只喂得进一个变量（app.js 的 WARN_MAP），所以这两条只能用 {n}
  assert.deepEqual(placeholders(one("en", "lint.fork.noquestion")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "lint.fork.noquestion")), ["n"]);
  assert.deepEqual(placeholders(one("en", "lint.fork.open")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "lint.fork.open")), ["n"]);
  assert.deepEqual(placeholders(one("en", "lint.alt.lone")), []);
});

test("title= 里不写 ** 和 ` —— 那些地方走 t()，标记不会展开，只会原样显示给用户", () => {
  // tHtml 才展开行内标记；tooltip / placeholder 是 textContent 级的目的地。
  // 这条以前靠自觉，这一轮新增了二十多条 .title，改成机器管。
  for (const lang of ["en", "zh"]) {
    for (const [k, v] of values(lang)) {
      if (!/\.title$/.test(k) && !/placeholder$/.test(k)) continue;
      assert.ok(!/[`]/.test(v) && !/\*\*/.test(v),
        `${lang}.${k} 在 tooltip/placeholder 里写了行内标记：${v}`);
    }
  }
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

/* ------------------------------------------------ ⑦ 补上的四组：接缝上那几条
 *
 * 这四组的共同点是**当时谁都没敢加**：core 发了 code，strings 那位怕加了没人接、
 * web 那位怕指向一个不存在的 key，两边互相等，于是英文界面上原样漏出中文整句。
 * 点名在这里，就没有「两边一样地缺、测试全绿」这条路了。 */

test("⑦ `branch:` 拼错时那条降级提醒有文案 —— 否则英文界面上漏出一整句中文", () => {
  needKeys(["lint.branch.unknown"],
    "core 的 bad_branch 认不出 key 就退回服务端那句中文，而它恰恰是给刚打错字的人看的");
  for (const lang of ["en", "zh"]) {
    assert.deepEqual(placeholders(one(lang, "lint.branch.unknown")), ["branch"],
      `${lang}.lint.branch.unknown 得说出拼错的是哪个值`);
    // 必须说清后果：那个标记现在一点作用都没有，而不是「格式不对」
    assert.ok(/extends/.test(one(lang, "lint.branch.unknown")),
      `${lang}.lint.branch.unknown 没说它被当成什么处理了`);
  }
});

test("⑦ 「问题写了、候选还没标」有自己的一句话，而且说得出下一步", () => {
  needKeys(["lint.fork.nocandidates", "decision.question.orphan"],
    "写下的问题在页面上看得见、周围却什么都没有，人只会以为没保存成功");
  for (const lang of ["en", "zh"]) {
    for (const k of ["lint.fork.nocandidates", "decision.question.orphan"]) {
      assert.ok(/alternative/.test(one(lang, k)),
        `${lang}.${k} 只说了「还不是岔路口」，没说该做什么`);
    }
    // 和「未决」同一条规矩：这不是错，别写成责备。人一被责备就会去删那句话，
    // 而它是整套东西里唯一只能人写的信息。
    assert.ok(!/\bshould have\b|\bforgot|\berror\b|\bmissing\b/i.test(one("en", "decision.question.orphan")),
      `en.decision.question.orphan 像在责备用户：${one("en", "decision.question.orphan")}`);
    assert.ok(!/错误|失败|忘了|遗漏/.test(one("zh", "decision.question.orphan")),
      `zh.decision.question.orphan 像在责备用户：${one("zh", "decision.question.orphan")}`);
  }
});

test("⑦ 「都不行」逐字就是这三个字 —— README / FORMAT.md / MCP 三处都这么承诺的", () => {
  // 曾经是「全部作废」。图例说谎比没有图例更糟，而且「作废」听着像这几条记录
  // 出了问题被撤销了 —— 它们没有，它们各自都是走到底的结论（P4）。
  assert.equal(one("zh", "decision.alldead"), "都不行");
  assert.ok(/选 1/.test(one("zh", "decision.pick")));
  assert.ok(/已定/.test(one("zh", "decision.settled")));
});

test("⑦ 移动一个候选之后说得出两个岔路口各剩下谁", () => {
  needKeys(["toast.moved.fork", "toast.moved.fork.roots"],
    "组是派生的，事后重新拉一遍森林只看得到「现在是什么样」，看不到「刚才改了什么」");
  assert.deepEqual(placeholders(one("en", "toast.moved.fork")), ["at", "n"]);
  assert.deepEqual(placeholders(one("zh", "toast.moved.fork")), ["at", "n"]);
  // 根之间那一组没有分叉点 id 可说，所以它只吃 n
  assert.deepEqual(placeholders(one("en", "toast.moved.fork.roots")), ["n"]);
  assert.deepEqual(placeholders(one("zh", "toast.moved.fork.roots")), ["n"]);
});

test("⑦ 搜索命中「当时在决定什么」时，那一档有名字", () => {
  needKeys(["search.where.fork"], "否则英文界面上原样漏出 fork 这个词");
});

/* ------------------------------------------------ ⑧ 两条路径：开发路径 / 定稿流程
 *
 * 这一组挡的东西和别处不同：⑦ 挡的是「一条边什么意思」，这里挡的是
 * **「我现在在看哪一条路径」**。一个人第一次看见两个视图，全靠两个名字和各一句话
 * 分清自己在看什么；分不清的后果不是少读一句解释，是拿开发路径去写论文，
 * 或者以为定稿流程把自己的记录删掉了一半。
 */

test("⑧ 两条路径各有名字和一句解释 —— 这两行是全部的关键", () => {
  needKeys([
    "app.view.dev", "app.view.dev.title",
    "app.view.pipeline", "app.view.pipeline.title",
    "pipeline.name", "pipeline.lead",
    "pipeline.dev.name", "pipeline.dev.lead",
    "pipeline.pair.note",
  ], "两个视图并排站着，名字和那一句话就是读者唯一的分辨依据");

  // 切换器上的名字要短（贴着别的档并排放），解释要真的解释
  for (const lang of ["en", "zh"]) {
    for (const k of ["app.view.dev", "app.view.pipeline"]) {
      assert.ok(one(lang, k).length <= 20, `${lang}.${k} 太长，塞不进视图切换器：${one(lang, k)}`);
    }
    for (const k of ["pipeline.lead", "pipeline.dev.lead"]) {
      assert.ok(one(lang, k).length > 30, `${lang}.${k} 只是个名字，没说这条路径是干什么的`);
    }
  }
});

test("⑧ 两条路径的解释必须点名各自的读者 —— 「给自己查问题」对「给别人照着做」", () => {
  // 只说「全部记录」和「一条链」的话，人分不出该看哪一个。这套文案的分辨点
  // 从来不是内容多少，而是**读者是谁**。
  assert.match(one("en", "app.view.dev.title"), /yourself/i);
  assert.match(one("en", "app.view.pipeline.title"), /everyone else|someone else|others/i);
  assert.match(one("zh", "app.view.dev.title"), /给自己/);
  assert.match(one("zh", "app.view.pipeline.title"), /给别人/);
});

test("⑧ 「定稿流程不是开发路径的精简版」这句话不许丢", () => {
  // 它挡的是最容易犯的那个误解：以为两个视图是同一份东西的详略两版，于是
  // 开始找「为什么这一步被藏起来了」。两者回答的是两个不同的问题。
  const en = one("en", "pipeline.pair.note"), zh = one("zh", "pipeline.pair.note");
  assert.match(en, /how you got there/i, "英文里要说出「怎么走到那儿的」");
  assert.match(en, /what to do/i, "英文里要说出「该怎么做」");
  assert.match(zh, /怎么走到那儿的|怎么走到这儿的/);
  assert.match(zh, /该怎么做/);
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "pipeline.pair.note").length > 80,
      `${lang}.pipeline.pair.note 太短，说不清两者不是包含关系`);
  }
});

test("⑧ 空状态是门面不是报错：说清是什么、为什么值得声明、怎么声明", () => {
  // 一个 result 都没声明是绝大多数项目的初始状态，所以这几句是大多数人
  // 第一次点进定稿流程时唯一会读到的东西。写成「暂无数据」等于这功能没做。
  needKeys([
    "pipeline.empty.title", "pipeline.empty.what",
    "pipeline.empty.why", "pipeline.empty.how", "pipeline.empty.act",
  ], "没有成果时定稿流程视图上只剩这几句话");

  for (const lang of ["en", "zh"]) {
    for (const k of ["pipeline.empty.what", "pipeline.empty.why"]) {
      assert.ok(one(lang, k).length > 60, `${lang}.${k} 太短，回答不了「这是什么、为什么要用」`);
    }
    // 「怎么声明」得给出那一行的真实写法，不能只说「去声明一个成果」
    assert.match(one(lang, "pipeline.empty.how"), /result:/,
      `${lang}.pipeline.empty.how 没写出 result: 那一行长什么样`);
  }
  // 不许写成报错 / 空数据口吻——这是一句邀请，不是一次故障
  const en = ["title", "what", "why"].map((s) => one("en", "pipeline.empty." + s)).join(" ");
  const zh = ["title", "what", "why"].map((s) => one("zh", "pipeline.empty." + s)).join(" ");
  assert.ok(!/\berror\b|\bfail(ed|ure)?\b|\binvalid\b|nothing to show|no data\b/i.test(en),
    "en 的空状态用了报错口吻：" + en);
  assert.ok(!/错误|失败|无数据|暂无/.test(zh), "zh 的空状态用了报错口吻：" + zh);
});

test("⑧ 空状态要指向那个动作 —— {act} 让按钮名和这句话不会各说各的", () => {
  assert.deepEqual(placeholders(one("en", "pipeline.empty.how")), ["act"]);
  assert.deepEqual(placeholders(one("zh", "pipeline.empty.how")), ["act"]);
  needKeys(["pipeline.empty.act"], "空状态那句话里的 {act} 就是它");
});

test("⑧ 成果：标记 / 取消 / 说明 / 多个成果，都有文案", () => {
  needKeys([
    "pipeline.result.head", "pipeline.result.lead",
    "pipeline.result.entry", "pipeline.result.entry.bare",
    "pipeline.result.badge", "pipeline.result.badge.title",
    "pipeline.result.mark", "pipeline.result.mark.title",
    "pipeline.result.unmark", "pipeline.result.unmark.title",
    "pipeline.result.prompt", "pipeline.result.multi",
    "toast.result.marked", "toast.result.unmarked",
  ], "「哪一步是成果」是唯一声明出来的事，声明它的那套界面缺一环就用不了");

  assert.deepEqual(placeholders(one("en", "pipeline.result.entry")), ["link", "what"].sort());
  assert.deepEqual(placeholders(one("zh", "pipeline.result.entry")), ["link", "what"].sort());
  assert.deepEqual(placeholders(one("en", "toast.result.marked")), ["id"]);
});

test("⑧ 「成员清单不存」要写出来 —— 不然人会去找那个列成员的编辑框", () => {
  // 存一份成员清单就是中心索引（P1 禁止）。界面不说这件事，人找不到清单
  // 会以为功能缺了一半，然后自己在正文里手写一份——那正是双真相源。
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "pipeline.derived.note").length > 60,
      `${lang}.pipeline.derived.note 说不清成员是怎么来的`);
    assert.match(one(lang, "pipeline.derived.note"), /input/,
      `${lang}.pipeline.derived.note 没说是沿 input 反推的`);
    assert.match(one(lang, "pipeline.derived.note"), /parent/,
      `${lang}.pipeline.derived.note 没说没声明输入时退回 parent`);
    assert.match(one(lang, "pipeline.derived.note"), /dead/,
      `${lang}.pipeline.derived.note 没说 dead 会被剔掉`);
  }
  assert.match(one("en", "pipeline.result.lead"), /written down|declared/i);
  assert.match(one("zh", "pipeline.result.lead"), /只有这一条是写下来的|其余全是读出来的/);
});

test("⑧ 「这一步为什么在流程里」四种来路都说得出", () => {
  // 闭包是算出来的，算出来的东西必须能解释自己——否则用户只能选择相信它。
  needKeys([
    "pipeline.why.result", "pipeline.why.input",
    "pipeline.why.parent", "pipeline.why.include",
  ], "成果本身 / 被谁当成输入 / 退回 parent / 人手留下的，四种来路");
  assert.deepEqual(placeholders(one("en", "pipeline.why.input")), ["id"]);
  assert.deepEqual(placeholders(one("zh", "pipeline.why.parent")), ["id"]);
});

test("⑧ 两个例外是写在那一步自己身上的，文案要说出这一点", () => {
  needKeys([
    "pipeline.include.badge", "pipeline.include.badge.title",
    "pipeline.exclude.badge", "pipeline.exclude.badge.title",
    "editor.pipeline.label", "editor.pipeline.auto",
    "editor.pipeline.include", "editor.pipeline.exclude",
    "editor.pipeline.hint", "editor.pipeline.note.label",
    "editor.pipeline.note.placeholder",
    "toast.pipeline.include", "toast.pipeline.exclude", "toast.pipeline.auto",
  ], "include/exclude 是每一步自己的声明，和 branch: 同一个套路");

  // 关键那半句：项目上没有清单。不写，人会去项目页找「流程成员」那一栏。
  assert.match(one("en", "editor.pipeline.hint"), /the project never holds a list|no list/i);
  assert.match(one("zh", "editor.pipeline.hint"), /不存成员清单|没有清单/);
  // 默认那一档要摆在最前面并且劝人别动：手工设的是事实，事实会过期
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "editor.pipeline.hint").length > 80,
      `${lang}.editor.pipeline.hint 太短，说不清什么时候才该动它`);
  }
});

test("⑧ 「不算流程」不是贬义：它成功了，只是不属于这个方法", () => {
  // 写成「被排除 / 无效 / 没用上」的话，人会不敢标——于是探索性的步骤全留在
  // 流程里，Methods 草稿变成一份没人跑得完的清单。
  for (const lang of ["en", "zh"]) {
    const badge = one(lang, "pipeline.exclude.badge");
    assert.ok(!/fail|invalid|useless|error|无效|失败|废|没用/i.test(badge),
      `${lang}.pipeline.exclude.badge 带贬义：${badge}`);
  }
  assert.match(one("en", "pipeline.exclude.badge.title"), /worked/i);
  assert.match(one("zh", "pipeline.exclude.badge.title"), /成功/);
});

test("⑧ 两条路径要能互相跳，而且 tooltip 说的是「去那边能看到什么」", () => {
  needKeys([
    "pipeline.badge", "pipeline.badge.title",
    "pipeline.jump.dev", "pipeline.jump.dev.title",
    "pipeline.jump.final", "pipeline.jump.final.title",
  ], "「这一步当时有 3 个候选，为什么选了它」正是两条路径都留着的意义");

  // 跳回开发路径的理由必须点名候选：那是这个跳转唯一非有不可的场景
  assert.match(one("en", "pipeline.jump.dev.title"), /candidate/i);
  assert.match(one("zh", "pipeline.jump.dev.title"), /候选/);
  // 徽章要说清它是派生的，没人勾选过
  assert.match(one("en", "pipeline.badge.title"), /nothing was ticked|not.*stored|worked out/i);
  assert.match(one("zh", "pipeline.badge.title"), /没有人勾选|算出来|追到的/);
});

test("⑧ 三条诊断都有文案：没成果 / 流程里有 dead / 流程里有 L0-L1", () => {
  needKeys([
    "pipeline.check.head",
    "pipeline.warn.noresult", "pipeline.warn.dead", "pipeline.warn.weak",
  ], "这三条是声明一个成果之后白拿的全部收益，缺一条那块面板就只有半句话");

  // 两条要指名道姓：说「流程里有 dead」而不说是哪一步，等于让人自己再找一遍
  for (const lang of ["en", "zh"]) {
    assert.deepEqual(placeholders(one(lang, "pipeline.warn.dead")), ["ids", "n"].sort(),
      `${lang}.pipeline.warn.dead 得说出是哪几步`);
    assert.deepEqual(placeholders(one(lang, "pipeline.warn.weak")), ["ids", "n"].sort(),
      `${lang}.pipeline.warn.weak 得说出是哪几步`);
  }
});

test("⑧ 「结果依赖着一条自己放弃的路」要让人当回事，但不能像在指责", () => {
  // 这是这三条里唯一的 warn 级：它多半意味着 dead 标记过期了，也可能意味着
  // 结果真的立在死胡同上。措辞要说清两种可能都值得现在知道——写成责备，
  // 人的第一反应会是把那个 dead 标记删掉，那是拿一条假结论换一块绿色。
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "pipeline.warn.dead").length > 80,
      `${lang}.pipeline.warn.dead 太短，说不清这意味着什么`);
    assert.match(one(lang, "pipeline.warn.dead"), /dead/,
      `${lang}.pipeline.warn.dead 没点出那一步是 dead`);
  }
  assert.match(one("en", "pipeline.warn.dead"), /gave up|abandon/i,
    "英文里要说出「这是你自己放弃过的路」");
  assert.match(one("zh", "pipeline.warn.dead"), /放弃/);
  // 不许指责：主语是结果和记录，不是「你忘了 / 你应该」
  assert.ok(!/\byou (should|must|forgot|failed)\b/i.test(one("en", "pipeline.warn.dead")),
    "en.pipeline.warn.dead 像在指责用户：" + one("en", "pipeline.warn.dead"));
  assert.ok(!/忘了|忘记|应该|必须改|错误/.test(one("zh", "pipeline.warn.dead")),
    "zh.pipeline.warn.dead 像在指责用户：" + one("zh", "pipeline.warn.dead"));
  // 两种可能都要摆出来，只说一种就是在替人下结论
  assert.match(one("en", "pipeline.warn.dead"), /either/i);
  assert.match(one("zh", "pipeline.warn.dead"), /要么/);
});

test("⑧ 「投稿前该补哪几步」要说清后果：别人跑不起来", () => {
  assert.match(one("en", "pipeline.warn.weak"), /L0|L1/);
  assert.match(one("zh", "pipeline.warn.weak"), /L0|L1/);
  assert.match(one("en", "pipeline.warn.weak"), /run/i, "英文里要说出「别人跑不起来」");
  assert.match(one("zh", "pipeline.warn.weak"), /跑不起来|跑不了/);
});

test("⑧ 流程的等级：整条链一个数、最弱的是谁、这对别人意味着什么", () => {
  needKeys([
    "pipeline.level.head", "pipeline.level.note", "pipeline.level.weakest",
    "pipeline.level.means.L0", "pipeline.level.means.L1", "pipeline.level.means.L2",
    "pipeline.level.means.L3", "pipeline.level.means.L4",
  ], "一个数回答「别人能不能照着做出来」，还得指名是哪一步拖的后腿");

  assert.deepEqual(placeholders(one("en", "pipeline.level.weakest")), ["link", "title"].sort());
  assert.deepEqual(placeholders(one("zh", "pipeline.level.weakest")), ["link", "title"].sort());
  // 「L2」这三个字对读者没有意义，五句话各自要把它翻成一句关于**别人**的话
  for (const lang of ["en", "zh"]) {
    for (const L of ["L0", "L1", "L2", "L3", "L4"]) {
      const s = one(lang, "pipeline.level.means." + L);
      assert.ok(s.length > 25, `${lang}.pipeline.level.means.${L} 太短，翻不出「这意味着什么」`);
    }
  }
  assert.match(one("en", "pipeline.level.note"), /weakest/i);
  assert.match(one("zh", "pipeline.level.note"), /最弱/);
});

test("⑧ 三个导出各有名字和一句说明，并且说清这是初稿", () => {
  needKeys([
    "export.head", "export.lead",
    "export.figure", "export.figure.note",
    "export.methods", "export.methods.note",
    "export.page", "export.page.note",
    "export.draft.note", "toast.export.ready",
  ], "图 / Methods 草稿 / 独立页面，三样都要说得出它是什么、能拿去干什么");

  // 最要紧的一句：不要让人以为可以直接投出去
  for (const lang of ["en", "zh"]) {
    assert.ok(one(lang, "export.draft.note").length > 60,
      `${lang}.export.draft.note 太短，挡不住「这份可以直接交」的误会`);
  }
  assert.match(one("en", "export.draft.note"), /draft/i);
  assert.match(one("zh", "export.draft.note"), /初稿/);
  assert.match(one("en", "export.draft.note"), /read every line|before this goes/i,
    "英文里要说出「发出去之前自己读一遍」");
  assert.match(one("zh", "export.draft.note"), /读一遍|发出去之前/);
  // 图那一条要点明黑白可读：这次不许只靠颜色
  assert.match(one("en", "export.figure.note"), /black and white|greyscale|grayscale|grey|gray/i);
  assert.match(one("zh", "export.figure.note"), /黑白|灰度/);
});

test("⑧ 导出是逐字节确定的，文案要把这件事说出来 —— 它决定了人该不该留拷贝", () => {
  // P3：视图是文件系统的纯函数。说出来，人才会去重新生成而不是把导出结果
  // 存进仓库——一份存下来的导出就是第二份真相。
  assert.match(one("en", "export.lead"), /same bytes|byte-identical/i);
  assert.match(one("zh", "export.lead"), /逐字节/);
  assert.match(one("en", "toast.export.ready"), /same bytes|byte-identical/i);
  assert.match(one("zh", "toast.export.ready"), /逐字节/);
});

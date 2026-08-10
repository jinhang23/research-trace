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

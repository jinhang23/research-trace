/* traceUtil — 不碰 DOM 的纯函数层。
 *
 * 单独一个作用域并在 node 下 module.exports 出去，是为了这些判断能被真正测到
 * （tests/app.test.js）。下面那一大坨界面代码依赖 document，在 node 里跑不起来，
 * 于是「洞察小节怎么切」「草稿键怎么拼」「搜索命中怎么截片段」这些最容易写错、
 * 出错后果又最重（覆盖别人写的洞察 / 丢草稿）的逻辑就永远没有回归保护。
 * 所以把它们从界面里剥出来。
 */
(function (global) {
  "use strict";

  /* FORMAT.md 第 10 节的五级。任何比较都用下标，不要比字符串。
     给人看的标签和判据说明从前写死在这里，现在在 i18n.js 的 trace.level.<L>
     和 trace.level.<L>.hint 上——这一层是纯函数层，在 node 里没有 DOM 也没有
     界面语言，让它持有界面文案就等于让它替界面决定说哪种语言。 */
  var LEVELS = ["L0", "L1", "L2", "L3", "L4"];
  /* failed 不降级——「试过，跑不起来，因为 checkpoint 被清了」不改变记录本身的
     完整度，但它是整条链上最该被人看见的一行，所以界面上单独给了个显眼的标签
     （i18n 的 trace.repro.failed）。这里只留状态本身。 */
  var REPRO_STATES = ["verified", "runnable", "failed", "unknown"];

  /* 必须和 trace_write.INSIGHT_SECTIONS 的四个值逐字一致：服务端的 _merge_insights
     只认这四个标题，前端切错一个字，那一节就会被当成「非洞察小节」留在磁盘上，
     用户在框里删掉它却发现删不掉。 */
  var INSIGHT_HEADINGS = ["核心想法", "有效", "无效", "坑"];
  var HEADING_RE = /^\s*#{1,6}\s+(.+?)\s*$/;

  function levelIndex(l) { var i = LEVELS.indexOf(l); return i < 0 ? 0 : i; }

  /* 把 project.md 正文切成 [{heading, lines}]；第一个标题之前的部分 heading 为 null。
     和 trace_write._split_sections 同构——两边切法不一致的话，前端以为自己只提交了
     洞察，服务端却按别的边界合并。 */
  function splitSections(body) {
    var out = [], head = null, buf = [];
    var flush = function () {
      var any = buf.some(function (l) { return l.trim(); });
      if (head !== null || any) out.push({ heading: head, lines: buf });
    };
    String(body == null ? "" : body).split("\n").forEach(function (line) {
      var m = HEADING_RE.exec(line);
      if (m) { flush(); head = m[1]; buf = [line]; }
      else buf.push(line);
    });
    flush();
    return out;
  }

  /* 「编辑洞察」那个框里该放什么、以及什么绝对不能放进去。
     editable = 前言 + 四个洞察小节（缺的补成空标题，好让 trace_insight 有地方追加）；
     others  = 其余小节，尤其是「## 已删除」里那些「为什么删的」——目录已经没了，
              那几行是 G4 唯一还能 grep 到的证据，所以它们只读、不进提交文本。 */
  function splitInsightBody(body) {
    var secs = splitSections(body);
    var editable = [], others = [], seen = {};
    secs.forEach(function (s) {
      var text = s.lines.join("\n").replace(/^\n+|\n+$/g, "");
      if (!text.trim()) return;
      if (s.heading === null || INSIGHT_HEADINGS.indexOf(s.heading) >= 0) {
        if (s.heading !== null) seen[s.heading] = 1;
        editable.push(text);
      } else {
        others.push({ heading: s.heading, text: text });
      }
    });
    INSIGHT_HEADINGS.forEach(function (h) { if (!seen[h]) editable.push("## " + h); });
    return { editable: editable.join("\n\n") + "\n", others: others };
  }

  /* 提交前的自检：用户可能把整段正文（含「## 已删除」）粘回框里。服务端会丢弃这些
     小节，但用户不知道自己白写了，所以前端要能提前说出来是哪几节。 */
  function foreignHeadings(text) {
    return splitSections(text).filter(function (s) {
      return s.heading !== null && INSIGHT_HEADINGS.indexOf(s.heading) < 0;
    }).map(function (s) { return s.heading; });
  }

  /* 草稿键。项目和步骤 id 都进键里：同一个浏览器同时开几个项目是常态，
     键撞了就是把 A 项目的草稿恢复到 B 项目的另一步上。两段各自 encodeURIComponent
     之后再用 ':' 拼——不编码的话 slug "a:b" + id "c" 和 slug "a" + id "b:c"
     会拼成同一个键，用户以为草稿丢了。

     lang 是同一个道理再走一遍：note.md 和 note.en.md 是两份各自要写的正文，
     共用一个键就是「写完中文切去写英文，回来发现中文稿被英文稿盖了」。
     原文（lang 为空）保持老键**一个字节都不变**——改了的话，升级前正写到一半
     的那份草稿会变成谁也找不到的孤儿。 */
  function draftKey(project, id, lang) {
    var base = "trace.draft:" + encodeURIComponent(String(project == null ? "" : project))
      + ":" + encodeURIComponent(String(id == null ? "" : id));
    lang = String(lang == null ? "" : lang);
    return lang ? base + ":" + encodeURIComponent(lang) : base;
  }

  /* 当前语言下该显示哪一份，以及要不要跟读者说明。
   *
   * 这个函数是「不许猜原文是什么语言」那条规矩的落点：note.md 没写 lang: 时
   * 返回的是 why="unknown"，界面只能说「显示的是原文」，不能说「显示的是中文原文」。
   * 一份记录里出现汉字不等于它该被当成中文记录——半句中文注释的英文笔记很常见，
   * 猜错了就是对读者说谎，而这套系统全部的价值就在于它说的话可信。
   *
   *   tr        当前语言的译文（{title, body}），没有就是 null
   *   fallback  是不是在显示原文
   *   why       ""         原文本来就是这个语言（note.md 自己声明的），什么都不用说
   *             "declared" 原文声明了别的语言 —— 可以说清那是哪一种
   *             "unknown"  原文没声明 —— 只能说「这是原文」
   */
  function pickLang(rec, lang) {
    var tr = (rec && rec.tr && rec.tr[lang]) || null;
    if (tr) return { tr: tr, fallback: false, why: "" };
    var own = (rec && rec.lang) || "";
    if (own && own === lang) return { tr: null, fallback: false, why: "" };
    return { tr: null, fallback: true, why: own ? "declared" : "unknown" };
  }

  /* 正文里出现的小节标题（`## X` 的 X）。 */
  function headingsIn(body) {
    var out = Object.create(null);
    String(body == null ? "" : body).split("\n").forEach(function (line) {
      var m = HEADING_RE.exec(line);
      if (m) out[m[1]] = 1;
    });
    return out;
  }

  /* 这段正文用的是哪一套小节名。**只查表，不做语种识别。**
   *
   * templates 是 {语言: 正文模板}（i18n 的 template.body，逐字对着
   * trace_core.SECTION_NAMES）。命中哪一套模板的标题就算哪一种——这是在查
   * 那张封闭词表，不是在猜语言：一份英文笔记里写着「结果：0.943」并不说明
   * 它该长出中文小节名，而 `## 为什么` 只可能来自中文那一套。
   * 认不出来返回 ""，让调用方自己决定退到哪儿。
   */
  function langByHeadings(body, templates) {
    var have = headingsIn(body);
    var langs = Object.keys(templates || {}).sort();   // 产物确定：不吃对象键序
    for (var i = 0; i < langs.length; i++) {
      var hs = headingsIn(templates[langs[i]]);
      for (var h in hs) if (have[h]) return langs[i];
    }
    return "";
  }

  /* 搜索的干草堆里**必须**包含所有译文：整个双语功能的意义就是「英文那一侧
     也能回答同一个问题」。只搜原文的话，界面切成英文之后搜 "contrastive"
     一条都搜不到，而那正是人打开搜索框的原因。

     `path:` / `code:` 的位置和说明同样在里面，理由是同一条：「best.pt 是哪一步
     产出的」是这两个键存在的主要用途，`grep -rn best.pt projects/` 一秒答得出，
     站内搜索答不出就等于比 grep 弱。判据和服务端的 search_hits、MCP 的
     trace_search 是同一份（core.locations_haystack），三处必须搜到同一批东西。 */
  function locationsHay(step) {
    var bits = [];
    (step.paths || []).forEach(function (p) { bits.push(p.location || "", p.note || ""); });
    (step.code || []).forEach(function (c) { bits.push(c.location || "", c.note || ""); });
    (step.inputs || []).forEach(function (i) { bits.push(i.note || ""); });
    return bits.filter(Boolean).join(" ");
  }
  function hay(step) {
    var tr = (step && step.tr) || {}, extra = "";
    Object.keys(tr).sort().forEach(function (l) {
      var e = tr[l] || {};
      extra += " " + (e.title || "") + " " + (e.name || "") + " " + (e.body || "");
    });
    return (step.id + " " + (step.title || "") + " " + (step.body || "") + " "
            + (step.tags || []).join(" ") + " " + locationsHay(step) + extra).toLowerCase();
  }
  function matches(step, q) {
    q = String(q || "").trim().toLowerCase();
    return !q || hay(step).indexOf(q) >= 0;
  }

  /* 命中片段。跨项目搜索的结果列表里，人要靠这一行判断「是不是我要找的那条」，
     所以截的是**命中处**的上下文，不是正文开头。 */
  function snippet(text, q, radius) {
    var s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    q = String(q || "").trim();
    radius = radius || 42;
    if (!q) return s.slice(0, radius * 2);
    var i = s.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return s.slice(0, radius * 2);
    var a = Math.max(0, i - radius), b = Math.min(s.length, i + q.length + radius);
    return (a > 0 ? "…" : "") + s.slice(a, b) + (b < s.length ? "…" : "");
  }

  /* ------------------------------------------------------------ 字节数分档
   *
   * 只算「用哪一档、数字是多少」，不出文案——单位名在 i18n（unit.b/kb/mb/gb/tb）。
   * 分到 GB / TB 这两档是有具体来历的：用户那条 57 GB 的 CIF 目录，只到 MB 会
   * 显示成「58366 MB」，而这个数正是要一眼看出「这是个大家伙」的那一个。
   */
  function sizeUnit(bytes) {
    // 没写 size 的路径必须返回 null 而不是「0 B」：Number(null) 是 0，
    // 而「这个目录 0 字节」和「没人量过这个目录」是完全不同的两句话。
    if (bytes === null || bytes === undefined || bytes === "") return null;
    var n = Number(bytes);
    if (!isFinite(n) || n < 0) return null;
    if (n < 1024) return { key: "unit.b", n: String(Math.round(n)) };
    if (n < 1048576) return { key: "unit.kb", n: (n / 1024).toFixed(1) };
    if (n < 1073741824) return { key: "unit.mb", n: (n / 1048576).toFixed(1) };
    if (n < 1099511627776) return { key: "unit.gb", n: (n / 1073741824).toFixed(1) };
    return { key: "unit.tb", n: (n / 1099511627776).toFixed(2) };
  }

  /* ------------------------------------------------------ 回写用的三个序列化
   *
   * 编辑器里的 path / input / code 三个框，存进去和读出来必须是同一套写法，
   * 否则「改一下标题」就会静默抹掉刚核对完的 role 和校验和——这三个函数逐字
   * 对着 trace_core.format_path / format_input / format_code，
   * tests/test_web.py 拿 Python 那一侧的真实实现逐条对过（不是各写各的）。
   */
  var PATH_ROLES = ["input", "script", "output", "evidence"];
  function attrText(attrs) {
    var out = [];
    Object.keys(attrs || {}).forEach(function (k) { out.push(k + "=" + attrs[k]); });
    return out.join(" ");
  }
  function formatPath(p) {
    var segs = [String((p && p.location) || "").trim()];
    var role = String((p && p.role) || "").trim();
    if (PATH_ROLES.indexOf(role) >= 0) segs.push(role);
    var note = String((p && p.note) || "").trim();
    if (note) segs.push(note);
    var a = attrText(p && p.attrs);
    if (a) segs.push(a);
    return segs.join(" | ");
  }
  function formatInput(i) {
    var note = String((i && i.note) || "").trim();
    return String((i && i.step) || "") + (note ? " | " + note : "");
  }

  /* 建子步骤时从父步骤**抄一份路径**，但这几个属性一个都不能跟着抄。
     它们全是「某人在某一刻真的去看过一眼」的结论：checked / missing 是那一次
     核对的判决和日期，md5 / sha256 / size / n 是那一刻那些字节的度量。
     照抄进一个刚刚建出来、还什么都没跑的步骤，就凭空造出一条**看起来像证据**的
     假记录——比没有记录有害得多，而这一整条 ③ 需求的来历正是「假结论比没结论贵」。
     最荒唐的一种是 missing=：一个今天才建出来的步骤，一出生就声称那份数据没了。

     位置、role、说明是人写的判断，那三样恰恰**应该**继承（同一条线上数据在哪
     多半没变，改比重打省事）。认不出来的属性也留着：我们不知道 `nodes=…` 是
     度量还是描述，替人删掉别人写的字比多留一个字更糟。 */
  var MEASURED_ATTRS = ["size", "n", "md5", "sha256", "checked", "missing"];
  function inheritPath(p) {
    var attrs = {};
    Object.keys((p && p.attrs) || {}).forEach(function (k) {
      if (MEASURED_ATTRS.indexOf(k) < 0) attrs[k] = p.attrs[k];
    });
    return { location: (p && p.location) || "", role: (p && p.role) || "",
             note: (p && p.note) || "", attrs: attrs };
  }
  function formatCode(c) {
    var segs = [String((c && c.kind) || "").trim(), String((c && c.location) || "").trim()];
    var note = String((c && c.note) || "").trim();
    if (note) segs.push(note);
    var a = attrText(c && c.attrs);
    if (a) segs.push(a);
    while (segs.length > 1 && !segs[segs.length - 1]) segs.pop();
    return segs.join(" | ");
  }

  /* -------------------------------------------------------------- 洞察解析
   *
   * 和 trace_core.parse_insights 同构。浏览器手里只有 project.md 的原文，
   * 而「哪条被哪条取代了」要按条渲染才做得出折叠——所以这里必须读得懂同一套写法。
   * 两处封闭词表（小节名、「取代」那个词）在 tests/test_web.py 里对着 trace_core
   * 逐字核过，防的就是「Python 认得、网页不认得」这种半边失效。
   *
   * superseded_by 是**派生**的：磁盘上只有取代者身上那半句话。 */
  var INSIGHT_KIND_BY_HEADING = {
    "核心想法": "idea", "Ideas": "idea",
    "有效": "works", "Works": "works",
    "无效": "fails", "Doesn't work": "fails",
    "坑": "pitfall", "Pitfalls": "pitfall",
  };
  var SUPERSEDE_WORDS = ["取代", "supersedes"];
  var INSIGHT_ID_RE = /^`([A-Za-z][A-Za-z0-9_-]{0,15})`\s*([\s\S]*)$/;
  var BULLET_RE = /^\s*[-*]\s+(.*\S)\s*$/;
  var SUPERSEDE_RE = new RegExp(
    "\\s*·\\s*(?:" + SUPERSEDE_WORDS.join("|") + ")\\s+([A-Za-z][A-Za-z0-9_,\\s-]*)$");

  function parseInsightLine(text) {
    var sup = [];
    var m = SUPERSEDE_RE.exec(text);
    if (m) {
      sup = m[1].trim().split(/[,\s]+/).filter(Boolean);
      text = text.slice(0, m.index).replace(/\s+$/, "");
    }
    var iid = "";
    var m2 = INSIGHT_ID_RE.exec(text);
    if (m2) { iid = m2[1]; text = m2[2].trim(); }
    return { id: iid, text: text, supersedes: sup, superseded_by: [] };
  }

  function parseInsights(body) {
    var out = { idea: [], works: [], fails: [], pitfall: [] };
    var lines = String(body == null ? "" : body).split("\n");
    var kind = null, level = 0;
    lines.forEach(function (line, i) {
      var h = HEADING_RE.exec(line);
      if (h) {
        var lv = (/^\s*(#{1,6})/.exec(line))[1].length;
        var k = INSIGHT_KIND_BY_HEADING[h[1].trim()];
        if (k) { kind = k; level = lv; }
        // 更深的子标题不结束本节——和 trace_core.sections() 同一套层级语义
        else if (kind !== null && lv <= level) kind = null;
        return;
      }
      if (kind === null) return;
      var b = BULLET_RE.exec(line);
      if (!b) return;
      var item = parseInsightLine(b[1]);
      item.line = i;
      item.raw = b[1];
      out[kind].push(item);
    });
    var byId = Object.create(null);
    Object.keys(out).forEach(function (k) {
      out[k].forEach(function (it) { if (it.id && !byId[it.id]) byId[it.id] = it; });
    });
    Object.keys(out).forEach(function (k) {
      out[k].forEach(function (it) {
        it.supersedes.forEach(function (t) {
          var target = byId[t];
          if (target && target !== it) target.superseded_by.push(it.id || it.text.slice(0, 20));
        });
      });
    });
    return out;
  }

  /* ------------------------------------------------------------ ① 移动校验
   *
   * 服务端一定会再判一次（它才是唯一权威），但成环和「挂到自己的后代下面」
   * 必须**当场**说出来：这两条不是笔误，是想法本身有问题，等一个 4xx 回来再说
   * 已经晚了——人那时已经点过确定，注意力也已经离开了。
   * 返回空串表示可以移。 */
  function moveError(byId, id, parent) {
    if (!byId[id]) return "missing";
    parent = String(parent || "");
    if (!parent) return (byId[id].parent || "") ? "" : "noop";
    if (parent === id) return "self";
    if (!byId[parent]) return "missing";
    if ((byId[id].parent || "") === parent) return "noop";
    var cur = parent, seen = Object.create(null);
    while (cur && byId[cur] && !seen[cur]) {
      seen[cur] = 1;
      if (cur === id) return "descendant";       // 新父在自己的子树里
      cur = byId[cur].parent;
    }
    return "";
  }

  /* -------------------------------------------------------------- ⑥ 提示级
   *
   * 这三条诊断服务端发的是 warn 级，但它们**不影响 L0–L4**。混在真警告里显示，
   * 人很快就不再看警告栏了——而警告栏里那些真的会降级的条目才是要人动手的。 */
  var HINT_CODES = ["section_without_prose", "table_without_explanation", "code_without_explanation"];
  function warnLevel(w) {
    if (w && HINT_CODES.indexOf(w.code) >= 0) return "hint";
    return (w && w.level === "error") ? "error" : "warn";
  }

  /* ------------------------------------------------------------ ② 数据流布局
   *
   * 森林是单父树，数据流是 DAG——Reingold–Tilford 那一套（父居中于子）在 DAG 上
   * 根本没有定义。这里用最朴素也最稳的做法：**分层 + 直连边**。
   *
   *   层号 = 依赖里最深的那一个 + 1（最长路径）。于是每条边都朝下走，
   *          「谁先算出来的」在纵轴上是可读的。
   *   层内序 = 依赖的平均列号（重心法）排一遍，平局按森林序。
   *
   * 刻意**不做力导向**：它每次刷新形状都不一样，而形状本身是信息——
   * 「这张图和我上次看到的是同一张」是读图的前提。这里的输出是纯函数，
   * 同一份数据永远得到同一张图。
   *
   * 环（服务端会报 input_cycle）不会让它死循环：回边不参与层号计算，只照常画出来。
   */
  function flowLayout(steps, opts) {
    opts = opts || {};
    var NW = opts.nw || 148, NH = opts.nh || 44;
    var GX = opts.gx || 22, GY = opts.gy || 52, PAD = opts.pad || 14;
    var list = steps || [];
    var byId = Object.create(null);
    list.forEach(function (s) { byId[s.id] = s; });
    var order = list.map(function (s) { return s.id; });
    var ordIdx = Object.create(null);
    order.forEach(function (id, i) { ordIdx[id] = i; });

    /* 依赖边：parent ∪ inputs，去重、跳过悬空与自指——和 trace_core.dep_edges
       同一条规矩。kind 记的是「这条边同时是树边还是只是数据边」，图例分三档
       正是因为「parent 和 input 是同一步」占了绝大多数边，不单独给它一档，
       读者会以为数据流图画错了。 */
    var deps = Object.create(null), edges = [], kindOf = Object.create(null);
    order.forEach(function (id) {
      var s = byId[id], seen = Object.create(null), row = [];
      var add = function (t, kind) {
        if (!t || t === id || !byId[t]) return;
        if (seen[t]) { if (kindOf[t + ">" + id] !== kind) kindOf[t + ">" + id] = "both"; return; }
        seen[t] = 1;
        row.push(t);
        kindOf[t + ">" + id] = kind;
        edges.push({ from: t, to: id, kind: kind });
      };
      add(s.parent, "tree");
      (s.inputs || []).forEach(function (i) { add(i.step, "data"); });
      deps[id] = row;
    });
    edges.forEach(function (e) { e.kind = kindOf[e.from + ">" + e.to]; });

    var layer = Object.create(null), onStack = Object.create(null);
    order.forEach(function (start) {
      if (layer[start] !== undefined) return;
      var stack = [[start, 0]];
      while (stack.length) {
        var top = stack[stack.length - 1], id = top[0];
        if (layer[id] !== undefined) { stack.pop(); onStack[id] = 0; continue; }
        if (top[1] === 0) {
          top[1] = 1;
          onStack[id] = 1;
          deps[id].forEach(function (d) {
            if (layer[d] === undefined && !onStack[d]) stack.push([d, 0]);
          });
          continue;
        }
        var m = -1;
        deps[id].forEach(function (d) { if (layer[d] !== undefined && layer[d] > m) m = layer[d]; });
        layer[id] = m + 1;
        onStack[id] = 0;
        stack.pop();
      }
    });

    var rows = [];
    order.forEach(function (id) {
      var l = layer[id] || 0;
      while (rows.length <= l) rows.push([]);
      rows[l].push(id);
    });
    var col = Object.create(null);
    rows.forEach(function (ids, l) {
      if (l > 0) {
        ids.sort(function (a, b) {
          var ba = bary(a), bb = bary(b);
          if (ba !== bb) return ba - bb;
          return ordIdx[a] - ordIdx[b];
        });
      }
      ids.forEach(function (id, i) { col[id] = i; });
      function bary(id) {
        var got = deps[id].filter(function (d) { return col[d] !== undefined; });
        if (!got.length) return ordIdx[id];
        var sum = 0;
        got.forEach(function (d) { sum += col[d]; });
        return sum / got.length;
      }
    });

    var nodes = Object.create(null), wide = 0;
    order.forEach(function (id) {
      var l = layer[id] || 0, c = col[id] || 0;
      if (c + 1 > wide) wide = c + 1;
      nodes[id] = { x: PAD + c * (NW + GX), y: PAD + l * (NH + GY), layer: l, col: c };
    });
    return {
      nodes: nodes, edges: edges, layers: rows.length, nw: NW, nh: NH,
      w: list.length ? PAD * 2 + wide * (NW + GX) - GX : 0,
      h: list.length ? PAD * 2 + rows.length * (NH + GY) - GY : 0,
    };
  }

  /* 上游闭包：本步的数据和记录到底是从哪些步骤流过来的（含自己）。
     数据流视图里的「淡出」用的是它而不是 parent 那条链——在一张 DAG 上
     「祖先」这个词只有沿着依赖走才有意义。 */
  function depClosure(byId, id) {
    var out = Object.create(null), stack = id ? [id] : [];
    while (stack.length) {
      var cur = stack.pop();
      if (!cur || out[cur] || !byId[cur]) continue;
      out[cur] = 1;
      var s = byId[cur];
      if (s.parent) stack.push(s.parent);
      (s.inputs || []).forEach(function (i) { stack.push(i.step); });
    }
    return out;
  }

  var U = {
    LEVELS: LEVELS, REPRO_STATES: REPRO_STATES, INSIGHT_HEADINGS: INSIGHT_HEADINGS,
    INSIGHT_KIND_BY_HEADING: INSIGHT_KIND_BY_HEADING, SUPERSEDE_WORDS: SUPERSEDE_WORDS,
    PATH_ROLES: PATH_ROLES, HINT_CODES: HINT_CODES,
    levelIndex: levelIndex, splitSections: splitSections,
    splitInsightBody: splitInsightBody, foreignHeadings: foreignHeadings,
    draftKey: draftKey, matches: matches, snippet: snippet, locationsHay: locationsHay,
    pickLang: pickLang, headingsIn: headingsIn, langByHeadings: langByHeadings,
    sizeUnit: sizeUnit, formatPath: formatPath, formatInput: formatInput, formatCode: formatCode,
    MEASURED_ATTRS: MEASURED_ATTRS, inheritPath: inheritPath,
    parseInsights: parseInsights, parseInsightLine: parseInsightLine,
    moveError: moveError, warnLevel: warnLevel,
    flowLayout: flowLayout, depClosure: depClosure,
  };
  global.traceUtil = U;
  if (typeof module !== "undefined" && module.exports) module.exports = U;
})(typeof globalThis !== "undefined" ? globalThis : this);

/* app.js — 视图层。
 *
 * 交互契约（规格书第 7 节）：唯一状态是 selected，而 selected 就是 location.hash，
 * 所以连"唯一状态"都不需要一个变量来存——刷新、分享链接、前进后退全都自然正确。
 *
 * 两个视图（图 / 列表）共用同一份数据和同一套选中逻辑：布局是纯函数算好的，
 * 视图只负责把坐标画出来。
 *
 * 编辑器是 markdown + 实时预览，不是所见即所得：note.md 必须保持人能直接读、
 * 能 grep、能 diff。为此把"插入图片/表格"的成本压到最低——截图直接粘贴、
 * 表格从 Excel 直接粘贴——而不是引入一个会生成 HTML 的富文本编辑器。
 */
(function () {
  "use strict";

  // 没有 DOM 就只是被 node 载进来取上面那层纯函数，界面一个字都不该启动。
  if (typeof document === "undefined") return;

  var U = window.traceUtil;

  var BASE = window.TRACE_BASE || "";
  var MODE = window.TRACE_MODE === "static" ? "static" : "server";
  var PROJECT = window.TRACE_PROJECT || "";
  var esc = window.md.esc;
  var $ = function (s) { return document.querySelector(s); };

  /* 界面文案。t() 出纯文本（textContent / title / placeholder / prompt / toast），
     tHtml() 出 HTML（拼进 innerHTML 的模板）——把 t() 拼进 innerHTML 是这一页
     唯一能把用户数据变成可执行 HTML 的路径，两者绝不混用。约定和理由都在
     web/i18n.js 的文件头。
     这里不做「i18n 没载进来就退回中文」的兜底：静默回退会让漏接线的地方永远
     发现不了，而少一个 <script> 的后果本来就该在第一秒当场炸。
     调用一律写全 i18n.t(...) / i18n.tHtml(...)，不做 var t = i18n.t 这种缩写：
     这个文件里已经有七八个叫 t 的局部变量（s.trace、TOOLS 项、令牌、tagName…），
     缩写会被就近的那个静默遮住，而遮住之后的报错离现场很远。 */
  var i18n = window.i18n;
  function uiLang() { return i18n.lang(); }

  var F = { steps: [], order: [], lanes: {}, lane_count: 0, warnings: [], row_h: 28, lane_w: 14,
            tree: { nodes: {}, w: 0, h: 0, node_w: 176, node_h: 58 } };
  var IDX = {};
  var PROJECTS = [];
  var query = "";
  var editing = false;
  /* 窄屏（手机、竖着的平板）第一次打开默认走列表视图。
     图视图的画布宽度是布局算好的绝对像素，一棵稍微宽一点的树在 375px 上就是
     「一屏只看得见一个节点，全靠横向拖」——那不是可视化，是拼图。列表视图的轨道
     图在窄屏上依然完整可读（轨道只有十几像素宽），所以窄屏的默认答案是列表，
     图视图仍然一键可切、并且切过去会自动缩放到适应宽度。
     只在**没有存过偏好**时这样选：用户明确切过就永远听用户的。 */
  var savedView = localStorage.getItem("trace.view");
  var NARROW = 760;
  var VIEWS = ["graph", "list", "flow"];
  var view = VIEWS.indexOf(savedView) >= 0 ? savedView
    : (window.innerWidth && window.innerWidth < NARROW ? "list" : "graph");
  var zoom = 1;

  var IMG = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
  var RAIL_PAD = 12;

  /* ---------------------------------------------------------- 内容语言
   *
   * 界面语言和内容语言是两件事，这一段是全篇最容易做错的地方。
   *
   * `## 为什么` 那五行不是界面文案，它们是要写进 note.md 的**内容**：
   * trace_core 按这张封闭词表（SECTION_NAMES）去正文里找「为什么」「结论」，
   * 找不到就判 L0、check 就报缺。所以模板跟的是**这份记录要写成哪种语言**，
   * 不是读者此刻把界面切成了哪种语言——界面英文的人完全可能用中文记笔记，
   * 给他插一套英文小节名，等于让他这一步的评级凭空掉到 L0。
   *
   * 那「这份记录要写成哪种语言」从哪来？按可信度排：
   *   1) note.md 自己声明的 lang:（唯一真凭据）
   *   2) 兄弟/父步骤正文里已经在用的那一套小节名（查 SECTION_NAMES 这张封闭
   *      词表，不是语种识别——见 U.langByHeadings 的注释）
   *   3) project.md 声明的 lang:
   *   4) 界面语言（什么线索都没有时的最后一档）
   * 而且它在新建对话框里是**可见可改**的一个下拉：猜出来的默认值必须能被
   * 当场推翻，否则第 4 档那一步猜错就成了没有出口的坑。
   */
  var TEMPLATES = { zh: "", en: "" };
  function templates() {
    TEMPLATES.zh = i18n.tIn("zh", "template.body");
    TEMPLATES.en = i18n.tIn("en", "template.body");
    return TEMPLATES;
  }
  function templateBody(l) { return i18n.tIn(l || uiLang(), "template.body"); }

  function guessContentLang(hintStep) {
    var chain = [];
    if (hintStep) chain.push(hintStep);
    // 没给参考步骤（新建根节点）就看最近写的那一步：同一个项目里的小节名分裂
    // 是最难查的一类问题——评级和 check 只会说「没写结论」，不会说「小节名对不上」。
    for (var i = F.steps.length - 1; i >= 0 && chain.length < 6; i--) chain.push(F.steps[i]);
    var byHead = "";
    for (var j = 0; j < chain.length; j++) {
      if (chain[j].lang) return chain[j].lang;
      if (!byHead) byHead = U.langByHeadings(chain[j].body || "", templates());
    }
    if (byHead) return byHead;
    var p = currentProject();
    if (p && p.lang) return p.lang;
    return uiLang();
  }

  /* -------------------------------------------------------------- 工具 */

  function token() { return localStorage.getItem("trace.token") || ""; }
  function setToken(t) {
    if (t) localStorage.setItem("trace.token", t); else localStorage.removeItem("trace.token");
    paintToken();
  }
  function paintToken() {
    var b = $("#btn-token");
    b.textContent = token() ? "🔓" : "🔒";
    b.title = i18n.t(token() ? "app.token.set" : "app.token.unset");
  }
  function canWrite() { return MODE === "server"; }

  /* 失败时抛的 Error 上挂着 status 和 data：409 冲突要把服务端连同错误一起返回的
     「当前内容」摆给人看，只留一句 message 就没得可摆了。 */
  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (token()) opts.headers["Authorization"] = "Bearer " + token();
    return fetch(BASE + path, opts).then(function (r) {
      return r.text().then(function (t) {
        var j = {};
        try { j = t ? JSON.parse(t) : {}; } catch (e) { j = { error: t.slice(0, 200) }; }
        if (!r.ok) {
          var err = new Error(j.error || r.status + " " + r.statusText);
          err.status = r.status;
          err.data = j;
          throw err;
        }
        return j;
      });
    });
  }
  function papi(path, opts) { return api("/api/p/" + encodeURIComponent(PROJECT) + path, opts); }

  function projectHref(slug) {
    if (MODE !== "static") return BASE + "/p/" + encodeURIComponent(slug) + "/";
    return (PROJECT ? "../../p/" : "p/") + encodeURIComponent(slug) + "/index.html";
  }
  function homeHref() {
    // ?all=1 是服务端那个「只剩一个项目就跳进去」的旁路。不带它的话，
    // 点「所有项目 ▸」会被 302 立刻弹回来，首页上的跨项目搜索永远够不着。
    if (MODE !== "static") return BASE + "/?all=1";
    return PROJECT ? "../../index.html" : "index.html";
  }

  var toastTimer = null;
  function toast(msg, isErr) {
    var el = $(".toast") || document.body.appendChild(Object.assign(document.createElement("div"), { className: "toast" }));
    el.className = "toast" + (isErr ? " err" : "");
    el.textContent = msg;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.remove(); }, isErr ? 7000 : 3000);
  }
  function fail(e) { toast(String(e && e.message || e), true); }

  function todayISO() {
    var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }
  /* 分档在纯函数层（U.sizeUnit），这里只负责把它说成话。GB / TB 两档是为
     path 上的 size= 加的：57 GB 那个目录只到 MB 会显示成「58366 MB」。 */
  function human(n) {
    var u = U.sizeUnit(n);
    return u ? i18n.t(u.key, { n: u.n }) : "";
  }
  function fileURL(s, rel) {
    var p = rel.split("/").map(encodeURIComponent).join("/");
    if (MODE === "static") return "steps/" + encodeURIComponent(s.dirname) + "/" + p;
    return BASE + "/p/" + encodeURIComponent(PROJECT) + "/files/" + encodeURIComponent(s.id) + "/" + p;
  }
  function resolverFor(s) { return function (h) { return fileURL(s, h); }; }
  function isAgent(s) { return (s.author || "").indexOf("agent") === 0; }

  /* ---------------------------------------------------------- 内容跟着语言走
   *
   * 一步在当前界面语言下该显示的标题和正文。图、列表、详情、面包屑、洞察面板
   * 全部走这两个函数——漏一处就会出现「树上是英文、点开是中文」。
   *
   * 图和附件**不跟着变**：译文是同一步的另一份文字，不是另一个步骤，
   * `![](fig.png)` 指的还是那一个目录里的那一张图。所以 fileURL / resolverFor
   * 一个字都没动，note.en.md 里写 `![](loss.png)` 和 note.md 里写它落到同一个
   * 文件上。这条如果破了，翻译一份就等于把所有图链接指向不存在的地方。
   */
  function stepTitle(s) {
    var pick = U.pickLang(s, uiLang());
    return (pick.tr && pick.tr.title) || (s && s.title) || "";
  }
  function stepBody(s) {
    var pick = U.pickLang(s, uiLang());
    return (pick.tr && pick.tr.body) || (s && s.body) || "";
  }
  function titleOf(id) { return IDX[id] ? stepTitle(IDX[id]) : ""; }

  function langName(code) {
    return i18n.has("lang." + code) ? i18n.t("lang." + code) : String(code || "");
  }

  /* 正文这一栏此刻显示的是不是原文，以及能不能说清原文是哪种语言。
     只翻了标题、正文还空着的译文也算回退——读者看到的确实是原文。 */
  function bodyFallback(rec) {
    var l = uiLang(), pick = U.pickLang(rec, l);
    if (pick.tr && String(pick.tr.body || "").trim()) return "";
    if (pick.tr) return (rec.lang && rec.lang !== l) ? "declared" : "unknown";
    return pick.fallback ? pick.why : "";
  }

  /* 回退时对读者的如实说明。两档，按「我们究竟知道什么」分：
   *   declared 原文声明了 lang: 且不是当前语言 → 一整句话，并说清那是哪一种语言
   *   unknown  原文没声明                     → 只说「这是原文」，绝不猜是哪种
   *
   * 第二档做成一个两三个字的徽章（整句在 title 里）而不是一条横幅：绝大多数
   * 项目根本没在做双语，一个 lang: 都没写，此时每一步顶一条「还没有译文」的
   * 横幅是纯噪声——而噪声会让第一档那条真正要紧的说明一起被跳过去。
   */
  function trNotice(rec, key) {
    var why = bodyFallback(rec);
    key = key || "tr.fallback.note";
    if (!why) return "";
    if (why === "unknown") {
      return '<p class="trnote quiet"><span class="trbadge" title="' + esc(i18n.t(key)) + '">'
        + esc(i18n.t("tr.badge.original")) + "</span></p>";
    }
    return '<p class="trnote"><span class="trbadge">' + esc(i18n.t("tr.badge.original")) + "</span>"
      + i18n.tHtml(key) + ' <span class="trlang">' + esc(langName(rec.lang)) + "</span></p>";
  }

  function selected() { var h = decodeURIComponent(location.hash.slice(1)); return IDX[h] ? h : ""; }
  /* 切换选中会把编辑器整个丢掉重渲染，所以先过一道离开确认。
     确认框只在**有未保存改动**时出现——每次点节点都弹一下才是真的烦人。 */
  function select(id) { guardLeave(function () { forceSelect(id); }); }
  function forceSelect(id) {
    editing = false;
    if (id) location.hash = "#" + encodeURIComponent(id);
    else history.replaceState(null, "", location.pathname + location.search);
    onHash();
  }
  function chainOf(id) {
    var set = Object.create(null), cur = id;
    while (cur && IDX[cur] && !set[cur]) { set[cur] = 1; cur = IDX[cur].parent; }
    return set;
  }

  /* -------------------------------------------------------------- 项目 */

  /* 项目名同样跟语言走：project.en.md 的 name 就是这个项目的英文名。
     没有译文就用原名——项目名不像正文，没地方摆一句「这是原文」，
     而一个突然变成中文的下拉项本身就说明了它没被翻译。 */
  function projectName(p) {
    if (!p) return "";
    var pick = U.pickLang(p, uiLang());
    return (pick.tr && pick.tr.name) || p.name || p.slug || "";
  }

  function renderProjects() {
    $("#proj").innerHTML = '<option value="">' + esc(i18n.t("app.proj.all")) + "</option>"
      + PROJECTS.map(function (p) {
          return '<option value="' + esc(p.slug) + '"' + (p.slug === PROJECT ? " selected" : "") + ">"
            + esc(i18n.t("app.proj.option", { name: projectName(p), n: p.steps })) + "</option>";
        }).join("");
  }

  function renderHome() {
    // 项目索引页的搜索框以前被 CSS 直接隐藏。现在它同时干两件事：
    // 筛项目卡片（下面这段），以及跨项目搜步骤正文（#hitlist）。
    var q = query.trim().toLowerCase();
    // 项目名的两种写法都能筛到：搜 "对比学习" 和搜 "contrastive" 都该找到同一个项目
    var list = q ? PROJECTS.filter(function (p) {
      return (p.name + " " + projectName(p) + " " + p.slug).toLowerCase().indexOf(q) >= 0;
    }) : PROJECTS;
    $("#cards").innerHTML = list.map(function (p) {
      var c = p.counts || {};
      var bar = p.steps
        ? ["done", "wip", "dead"].map(function (k) {
            return (c[k] || 0) ? '<i class="sg-' + k + '" style="flex:' + c[k] + '"></i>' : "";
          }).join("")
        : '<i class="sg-empty" style="flex:1"></i>';
      return '<a class="pcard" href="' + projectHref(p.slug) + '">'
        + "<h2>" + esc(projectName(p)) + "</h2>"
        + '<div class="pbar">' + bar + "</div>"
        + '<div class="pmeta mono">'
        + i18n.tHtml("home.card.meta", {
            steps: i18n.t("count.steps", { n: p.steps }),
            done: c.done || 0, wip: c.wip || 0, dead: c.dead || 0,
          })
        + (p.latest ? " · " + i18n.tHtml("home.card.latest", { date: p.latest }) : "") + "</div>"
        + (p.warnings ? '<div class="pwarn">'
            + i18n.tHtml("home.card.warnings", { warnings: i18n.t("count.warnings", { n: p.warnings }) })
            + "</div>" : "")
        + "</a>";
    }).join("") || '<p class="placeholder">'
      + (q ? i18n.tHtml("home.nofilter", { q: query.trim() }) : i18n.tHtml("home.empty"))
      + "</p>";
    $("#hits").textContent = q
      ? i18n.t("home.filter.count", { shown: list.length, total: PROJECTS.length }) : "";
  }

  /* -------------------------------------------------------------- 数据 */

  function apply(data) {
    F = data;
    IDX = Object.create(null);
    F.steps.forEach(function (s) { IDX[s.id] = s; });
    document.documentElement.style.setProperty("--row-h", (F.row_h || 28) + "px");
    renderRails();
    renderRows();
    renderDiagram();
    renderFlow();
    renderWarnings();
    renderMissingPaths();
    applyView();
    onHash();
  }
  function refresh() { return papi("/forest").then(apply).catch(fail); }
  function refreshProjects() {
    return api("/api/projects").then(function (d) {
      PROJECTS = d.projects || [];
      renderProjects();
      if (!PROJECT) { renderHome(); return; }
      // 洞察面板是从 PROJECTS 里读的，而它比 forest 晚到。
      // 不在这里重画一次的话，第一次打开项目看到的就是个空框。
      if (!selected() && !editing) renderDetail();
    }).catch(function () {});
  }

  /* -------------------------------------------------------------- 图视图 */

  function renderDiagram() {
    var T = F.tree || { nodes: {}, w: 0, h: 0, node_w: 176, node_h: 58 };
    var NW = T.node_w, NH = T.node_h;
    var svg = $("#dedges"), holder = $("#dnodes");
    $("#diagram").style.width = T.w + "px";
    $("#diagram").style.height = T.h + "px";
    svg.setAttribute("width", T.w);
    svg.setAttribute("height", T.h);
    svg.setAttribute("viewBox", "0 0 " + T.w + " " + T.h);

    var out = [];
    F.steps.forEach(function (s) {
      var p = s.parent && IDX[s.parent];
      if (!p || !T.nodes[p.id] || !T.nodes[s.id]) return;
      var a = T.nodes[p.id], b = T.nodes[s.id];
      var px = a.x + NW / 2, py = a.y + NH;
      var cx = b.x + NW / 2, cy = b.y;
      var tip = cy - 7, midY = (py + tip) / 2, d;
      if (Math.abs(px - cx) < 1) {
        d = "M" + px + " " + py + "V" + tip;
      } else {
        // 拐弯发生在两层之间的中线上：多个子节点从同一父分叉时，
        // 竖直段各走各的 x，视觉上是分开的，不会堆成一团。
        var r = Math.min(9, Math.abs(cx - px) / 2, (tip - py) / 2), dir = cx > px ? 1 : -1;
        d = "M" + px + " " + py + "V" + (midY - r)
          + "Q" + px + " " + midY + " " + (px + dir * r) + " " + midY
          + "H" + (cx - dir * r)
          + "Q" + cx + " " + midY + " " + cx + " " + (midY + r)
          + "V" + tip;
      }
      out.push('<path class="dedge s-' + s.status + '" data-id="' + esc(s.id) + '" d="' + d + '"/>');
      out.push('<path class="darrow s-' + s.status + '" data-id="' + esc(s.id) + '" d="M'
        + (cx - 4.5) + " " + tip + "L" + (cx + 4.5) + " " + tip + "L" + cx + " " + (tip + 7) + 'Z"/>');
    });
    svg.innerHTML = out.join("");

    holder.innerHTML = F.steps.map(function (s) {
      var n = T.nodes[s.id];
      if (!n) return "";
      var pics = (s.files || []).filter(function (f) { return IMG.test(f.path); }).length;
      var other = (s.files || []).length - pics;
      var marks = (pics ? '<span class="cmk" title="' + esc(i18n.t("count.images", { n: pics })) + '">🖼' + (pics > 1 ? pics : "") + "</span>" : "")
        + (other ? '<span class="cmk" title="' + esc(i18n.t("count.files", { n: other })) + '">📎' + (other > 1 ? other : "") + "</span>" : "")
        + nodeMarks(s);
      return '<div class="card s-' + s.status + '" data-id="' + esc(s.id) + '" tabindex="0"'
        + ' style="left:' + n.x + "px;top:" + n.y + "px;width:" + NW + "px;height:" + NH + 'px">'
        + '<div class="chead"><span class="cid">' + esc(s.id) + "</span>"
        + '<span class="cst">' + s.status + "</span>"
        + (isAgent(s) ? '<span class="cbot" title="' + esc(s.author) + '">🤖</span>' : "")
        + marks
        + '<span class="cdate">' + esc(s.date || "") + "</span></div>"
        + '<div class="ctitle">' + esc(stepTitle(s) || i18n.t("common.untitled")) + "</div>"
        + "</div>";
    }).join("");
    setZoom(zoom);
  }

  function setZoom(z) {
    zoom = Math.max(0.3, Math.min(2, z));
    var T = F.tree || { w: 0, h: 0 };
    $("#diagram").style.transform = "scale(" + zoom + ")";
    $("#dwrap").style.width = Math.ceil(T.w * zoom) + "px";
    $("#dwrap").style.height = Math.ceil(T.h * zoom) + "px";
    $("#zoomval").textContent = Math.round(zoom * 100) + "%";
  }
  function fitZoom() {
    var w = F.tree && F.tree.w;
    setZoom(w ? Math.min(1, ($("#scroller").clientWidth - 10) / w) : 1);
  }

  /* -------------------------------------------------------------- 数据流视图
   *
   * 第三个视图，画的是**另一张图**：森林是单父树，数据流是 DAG。016 的输入同时
   * 来自 013 和 014，树上只能表达一个——以前只能在正文里写一句「本步的输入其实
   * 来自 X」，读的人得自己拼。
   *
   * 布局在纯函数层（U.flowLayout）：分层 + 直连边，不做力导向。力导向每次刷新
   * 形状都不一样，而形状本身是信息。
   *
   * 边的样子只承载一件事——这条边是树边、数据边、还是两者同时。节点的 status
   * 仍然走线型（和另外两个视图一致），两个通道不打架。
   */
  var FLOW = { nodes: {}, edges: [], w: 0, h: 0, nw: 148, nh: 44 };

  function renderFlow() {
    FLOW = U.flowLayout(F.steps || []);
    var svg = $("#fedges"), holder = $("#fnodes");
    if (!svg || !holder) return;
    var NW = FLOW.nw, NH = FLOW.nh;
    $("#flow").style.width = FLOW.w + "px";
    $("#flow").style.height = FLOW.h + "px";
    svg.setAttribute("width", FLOW.w);
    svg.setAttribute("height", FLOW.h);
    svg.setAttribute("viewBox", "0 0 " + FLOW.w + " " + FLOW.h);

    var out = [];
    FLOW.edges.forEach(function (e) {
      var a = FLOW.nodes[e.from], b = FLOW.nodes[e.to];
      if (!a || !b) return;
      var x1 = a.x + NW / 2, y1 = a.y + NH, x2 = b.x + NW / 2, y2 = b.y - 7;
      var dy = Math.max(16, (y2 - y1) / 2);
      var d = "M" + x1 + " " + y1 + "C" + x1 + " " + (y1 + dy) + " " + x2 + " " + (y2 - dy) + " " + x2 + " " + y2;
      var tag = ' data-from="' + esc(e.from) + '" data-to="' + esc(e.to) + '"';
      // 「同时是父子边和数据边」画成一条粗的托底 + 一条细的在上面：它占了绝大多数边，
      // 不让它自成一档的话，读者会以为这张图把树边画丢了。
      if (e.kind === "both") out.push('<path class="fedge under"' + tag + ' d="' + d + '"/>');
      out.push('<path class="fedge k-' + e.kind + '"' + tag + ' d="' + d + '"/>');
      out.push('<path class="farrow k-' + e.kind + '"' + tag + ' d="M' + (x2 - 4) + " " + y2
        + "L" + (x2 + 4) + " " + y2 + "L" + x2 + " " + (y2 + 6.5) + 'Z"/>');
    });
    svg.innerHTML = out.join("");

    holder.innerHTML = (F.steps || []).map(function (s) {
      var n = FLOW.nodes[s.id];
      if (!n) return "";
      var off = (s.inputs || []).filter(function (i) { return i.step !== s.parent; }).length;
      return '<div class="fcard s-' + s.status + '" data-id="' + esc(s.id) + '" tabindex="0"'
        + ' style="left:' + n.x + "px;top:" + n.y + "px;width:" + NW + "px;height:" + NH + 'px">'
        + '<div class="chead"><span class="cid">' + esc(s.id) + "</span>"
        + (off ? '<span class="cmk dep" title="'
            + esc(i18n.t("count.inputs", { n: off })) + '">⇢' + off + "</span>" : "")
        + '<span class="cdate">' + esc(s.date || "") + "</span></div>"
        + '<div class="ctitle">' + esc(stepTitle(s) || i18n.t("common.untitled")) + "</div>"
        + "</div>";
    }).join("");

    // 一条 input 都没有时如实说明：那种情况下这张图和树逐字一样，
    // 不说的话人会以为是功能坏了。
    var any = (F.steps || []).some(function (s) { return (s.inputs || []).length; });
    $("#flowempty").hidden = any || !(F.steps || []).length;
  }

  /* -------------------------------------------------------------- 列表视图 */

  function renderRails() {
    var RH = F.row_h || 28, LW = F.lane_w || 14;
    var svg = $("#rails");
    var w = RAIL_PAD * 2 + Math.max(0, (F.lane_count || 1) - 1) * LW;
    var h = Math.max(F.steps.length * RH, 1);
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);

    var x = function (l) { return RAIL_PAD + l * LW; };
    var y = function (r) { return r * RH + RH / 2; };
    var out = [];

    F.steps.forEach(function (s) {
      var p = s.parent && IDX[s.parent];
      if (!p) return;
      var x1 = x(p.lane), y1 = y(p.row), x2 = x(s.lane), y2 = y(s.row), d;
      if (p.lane === s.lane) {
        d = "M" + x1 + " " + y1 + "V" + y2;
      } else {
        var R = Math.min(LW, RH / 2), dir = x2 > x1 ? 1 : -1;
        d = "M" + x1 + " " + y1 + "V" + (y2 - R) + "Q" + x1 + " " + y2 + " " + (x1 + dir * R) + " " + y2 + "H" + x2;
      }
      out.push('<path class="edge s-' + s.status + '" data-id="' + esc(s.id) + '" d="' + d + '"/>');
    });

    F.steps.forEach(function (s) {
      var cx = x(s.lane), cy = y(s.row);
      if (s.status === "dead") {
        out.push('<rect class="node s-dead" data-id="' + esc(s.id) + '" x="' + (cx - 3.4) + '" y="' + (cy - 3.4) + '" width="6.8" height="6.8"/>');
        if (!(s.children || []).length) {
          // 末端横杠：dead 不能只靠"变灰"表达——变灰的语义是"不相关"，不是"结论为否"
          out.push('<line class="stop" data-id="' + esc(s.id) + '" x1="' + (cx - 4.8) + '" y1="' + (cy + 6.6) + '" x2="' + (cx + 4.8) + '" y2="' + (cy + 6.6) + '"/>');
        }
      } else {
        out.push('<circle class="node s-' + s.status + '" data-id="' + esc(s.id) + '" cx="' + cx + '" cy="' + cy + '" r="3.6"/>');
      }
    });
    svg.innerHTML = out.join("");
  }

  function renderRows() {
    $("#rows").innerHTML = F.steps.map(function (s) {
      var pics = (s.files || []).length;
      return '<div class="row s-' + s.status + '" data-id="' + esc(s.id) + '">'
        + '<span class="id s-' + s.status + '">' + esc(s.id) + "</span>"
        + '<span class="t">' + esc(stepTitle(s) || i18n.t("common.untitled")) + "</span>"
        + nodeMarks(s)
        + (pics ? '<span class="who" title="' + esc(i18n.t("count.files", { n: pics })) + '">📎</span>' : "")
        + (isAgent(s) ? '<span class="who" title="' + esc(s.author) + '">🤖</span>' : "")
        + '<span class="d">' + esc(s.date || "") + "</span>"
        + "</div>";
    }).join("");
  }

  /* 图/列表上的可溯源提示。
   *
   * 规格书那条约束：一个视觉通道只承载一件事。线型已经被 status 占了，不透明度
   * 被「是否在祖先链上 / 是否命中搜索」占了，颜色只作线型的补强。所以这里用的是
   * **字形标记**这个第四通道——和已有的 🖼 📎 🤖 同一档：它是一小段可读文本，
   * 不改线型、不改不透明度、不给节点换色，打印和色盲下也一样看得见。
   *
   * 只标两种，不标满五级：满级标记会退化成噪声，而这两种是需要人去动手的——
   *   L0  这一步自己就断了链，别人（包括半年后的自己）复原不了
   *   ↺✕ 有人真去复现过并且失败了，这是最该被看见的一行结论
   */
  function traceMarks(s) {
    var out = "";
    var tr = s.trace || null;
    if (tr && tr.self === "L0") {
      // missing 是服务端算的中文清单（trace_core，Python 侧不在翻译范围），
      // 逐条过一遍 i18n.traceMissing 换成本语言的说法，认不出的原样保留——
      // 老老实实给中文，好过把这一条悄悄吞掉。
      var why = (tr.missing || []).slice(0, 3).map(function (m) { return i18n.traceMissing(m); })
        .join(" · ") || i18n.t("trace.level.L0.hint");
      out += '<span class="cmk lv0" title="' + esc(i18n.t("list.mark.l0", { why: why })) + '">L0</span>';
    }
    var last = (s.repro || [])[(s.repro || []).length - 1];
    if (last && last.state === "failed") {
      out += '<span class="cmk rfail" title="'
        + esc(last.note ? i18n.t("list.mark.reprofail.note", { note: last.note })
                        : i18n.t("list.mark.reprofail")) + '">↺✕</span>';
    }
    return out;
  }

  /* 图/列表上的其余三个字形标记。和 traceMarks 用的是同一个通道（字形），
   * 所以线型仍然只归 status、不透明度仍然只归祖先链/搜索命中。
   *
   *   ⇢n  这一步还有 n 个数据来源不在树上 —— 不给这个标记，数据流视图就是一个
   *       没人知道该去点的按钮，而 ② 的全部意义就在于「让人一眼分得清 parent
   *       和 inputs 是两回事」
   *   ⇄   被移动过 —— 用户吃过的亏是「创建日期和内容对不上号」，一棵移动过的树
   *       本来就会和创建顺序对不上，得让人知道那不是 bug
   *   ⊘n  有 n 个记下来的位置已经确认不存在了（那三个被删掉的目录，57 GB 的那个）
   */
  function nodeMarks(s) {
    var out = traceMarks(s);
    var off = (s.inputs || []).filter(function (i) { return i.step !== s.parent; }).length;
    if (off) {
      out += '<span class="cmk dep" title="'
        + esc(i18n.t("count.inputs", { n: off }) + " · " + i18n.t("input.parent.tip"))
        + '">⇢' + off + "</span>";
    }
    if ((s.moved || []).length) {
      out += '<span class="cmk moved" title="'
        + esc(i18n.t("move.badge.title", { n: s.moved.length })) + '">⇄</span>';
    }
    var gone = (s.paths || []).filter(function (p) { return p.state === "missing"; }).length;
    if (gone) {
      out += '<span class="cmk gone" title="' + esc(i18n.t("path.summary.missing", { n: gone }))
        + '">⊘' + (gone > 1 ? gone : "") + "</span>";
    }
    return out;
  }

  /* 服务端的 warning 是中文的（Python 侧不在翻译范围），而 ⑥ 之后警告栏里会混进
   * 一批**不影响等级**的提示。两件事都得在这里处理：
   *
   *  - 按 code 换成本语言的说法。占位符**优先取 w.vars**（core.warn 把句子里的
   *    那几个值结构化地一起发过来了）；只有拿不到 vars 时才退回从中文句子里抠。
   *    抠正则留着是为了老服务端 / 静态导出出来的旧数据，不是主路径——它脆得离谱，
   *    那几句中文改一个字就会让英文界面上原样漏出中文。抠不出来就显示原句，绝不吞。
   *  - 按级别分档。提示级和真警告混在一起显示，人很快就不再看警告栏了，
   *    而那里面真正会降级的条目才是要人动手的。
   */
  var WARN_MAP = {
    section_without_prose: { key: "lint.subheads", pick: /「[#\s]*([^」]+)」/, as: "section" },
    table_without_explanation: { key: "lint.table.nodesc" },
    code_without_explanation: { key: "lint.pre.nodesc" },
    dangling_input: { key: "input.warn.missing", pick: /(\S+)\s*$/, as: "id" },
    self_input: { key: "input.warn.self", pick: /[（(]([^）)]+)[）)]/, as: "id" },
    input_cycle: { key: "input.warn.cycle", pick: /:\s*([^—]+)/, as: "chain" },
  };
  /* 出的是 HTML：这些文案里有 `行内代码` 和 **粗体**，走 t() 的话反引号会原样
     显示给用户看。认不出来时退回服务端那句中文，那一条要 esc——它是别处来的字节。 */
  function warnText(w) {
    var m = WARN_MAP[w.code];
    if (!m || !i18n.has(m.key)) return esc(w.message);
    if (!m.pick) return i18n.tHtml(m.key);
    var vars = {};
    if (w.vars && typeof w.vars[m.as] === "string" && w.vars[m.as] !== "") {
      vars[m.as] = w.vars[m.as];
    } else {
      var got = m.pick.exec(w.message || "");
      if (!got) return esc(w.message);
      vars[m.as] = got[1].trim();
    }
    return i18n.tHtml(m.key, vars);
  }

  function renderWarnings() {
    var bar = $("#warnbar"), ws = F.warnings || [];
    bar.hidden = !ws.length;
    if (!ws.length) return;
    var by = { error: [], warn: [], hint: [] };
    ws.forEach(function (w) { by[U.warnLevel(w)].push(w); });
    var block = function (lv) {
      if (!by[lv].length) return "";
      var mark = lv === "error" ? "✕ " : (lv === "hint" ? "· " : "⚠ ");
      return '<div class="wgroup w-' + lv + '"><b class="wlv">'
        + esc(i18n.t("lint.level." + lv)) + "</b>"
        + (lv === "hint" ? '<span class="wnote">' + esc(i18n.t("lint.note")) + "</span>" : "")
        + by[lv].map(function (w) {
            return '<div class="wrow">' + mark + "<b>" + esc(w.where || w.code) + "</b> — "
              + warnText(w) + "</div>";
          }).join("") + "</div>";
    };
    bar.innerHTML = block("error") + block("warn") + block("hint");
  }

  /* 「整个项目里有几处位置已经不存在了」——用户这次是**手工核对**才发现三个目录
     没了（57 GB 的那个）。所以它不能只藏在某一步的详情里：一条横幅摆在顶上，
     点得进去。记录一条都不删，只是标出来（P4：那是一条发现，不是一个笔误）。 */
  function renderMissingPaths() {
    var bar = $("#missbar");
    if (!bar) return;
    var hits = [];
    (F.steps || []).forEach(function (s) {
      (s.paths || []).forEach(function (p) { if (p.state === "missing") hits.push(s.id); });
    });
    bar.hidden = !hits.length;
    if (!hits.length) return;
    var ids = hits.filter(function (id, i) { return hits.indexOf(id) === i; });
    bar.innerHTML = '<b>⊘ ' + esc(i18n.t("path.summary.missing", { n: hits.length })) + "</b>"
      + ids.map(function (id) { return " " + stepLink(id); }).join(" ·");
  }

  function applyView() {
    // 窄屏切到图视图时先自动适应宽度，否则第一眼是画布左上角那一小块
    if (view === "graph" && window.innerWidth < NARROW && F.tree && F.tree.w > $("#scroller").clientWidth) {
      fitZoom();
    }
    $("#dwrap").hidden = view !== "graph";
    $("#track").hidden = view !== "list";
    $("#fwrap").hidden = view !== "flow";
    $("#empty").hidden = F.steps.length > 0;
    $("#zoombar").hidden = view !== "graph";
    // 图例跟着视图换：数据流那三条说的是边的意思，和 status 那三条不是一回事，
    // 同时摆出来只会让人以为「实线 = 树边」。
    $("#treelegend").hidden = view === "flow";
    $("#flowlegend").hidden = view !== "flow";
    document.querySelectorAll("#viewtoggle button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-view") === view);
    });
    $("#legend").hidden = !F.steps.length;
  }

  /* -------------------------------------------------------------- 选中态 */

  function renderSelection() {
    var sel = selected(), chain = chainOf(sel);
    /* 数据流视图里「祖先」这个词只有沿着依赖走才有意义：那张图上淡出的判据是
       「这一步在不在选中那一步的上游闭包里」，也就是「这个数字是从哪些步骤
       流过来的」。通道没变，仍然是不透明度。 */
    var dchain = sel ? U.depClosure(IDX, sel) : {};
    var q = query.trim().toLowerCase(), hits = 0;

    document.querySelectorAll("#rows .row, #dnodes .card, #fnodes .fcard").forEach(function (el) {
      var id = el.getAttribute("data-id"), s = IDX[id];
      if (!s) return;
      // 判定走 U.matches：以前这里自己又拼了一遍干草堆，于是加上译文之后
      // 侧栏和跨项目搜索会对同一个词给出两种答案。
      var hit = U.matches(s, q);
      if (q && hit && el.classList.contains("row")) hits++;
      var inChain = el.classList.contains("fcard") ? dchain[id] : chain[id];
      el.classList.toggle("miss", !!q && !hit);
      el.classList.toggle("faded", !!sel && !inChain);
      el.classList.toggle("sel", id === sel);
    });
    document.querySelectorAll("#fedges [data-to]").forEach(function (el) {
      var from = el.getAttribute("data-from"), to = el.getAttribute("data-to");
      el.classList.toggle("faded", !!sel && !(dchain[from] && dchain[to]));
      el.classList.toggle("sel", sel === to || sel === from);
    });
    $("#hits").textContent = q ? hits + " / " + F.steps.length
      : (F.steps.length ? i18n.t("count.steps", { n: F.steps.length }) : "");

    document.querySelectorAll("#rails [data-id], #dedges [data-id]").forEach(function (el) {
      var id = el.getAttribute("data-id");
      el.classList.toggle("faded", !!sel && !chain[id]);
      el.classList.toggle("sel", id === sel && el.classList.contains("node"));
    });
  }

  function scrollToSelected() {
    var sel = selected();
    if (!sel) return;
    var sels = { graph: "#dnodes .card", flow: "#fnodes .fcard", list: "#rows .row" };
    var el = document.querySelector((sels[view] || sels.list) + '[data-id="' + sel + '"]');
    if (el) el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  /* -------------------------------------------------------------- 正文增强 */

  /* 指标表的数值列加一条底纹，长度按列内相对大小。
     纯粹是加成：单元格里的数字一个没动，LLM 读到的和渲染前一模一样，
     去掉这层样式也不丢任何信息。这正是「可视化从可读文本里长出来」那条原则。 */
  /* 数值判定不再由这里重做一遍。md.js 已经在渲染时判过「这一列是不是数值列」并把
     主数值写进了 <td data-num>（`0.943 ± 0.004` 取 0.943，误差项不参与）。这边再写
     一条正则就是两套判定，实测就分叉过：md.js 判定右对齐生效、这边判定 NaN 于是
     整列不画底纹，同一张表两种说法。所以只认 data-num。
     `—` 这类占位格没有 data-num，跳过它本身而不是把整列作废——一列里缺一个数
     不该让其余几行的对比消失。 */
  function numOf(td) {
    var raw = td.dataset ? td.dataset.num : undefined;
    return raw === undefined ? NaN : parseFloat(raw);
  }
  function barTables(root) {
    root.querySelectorAll("table").forEach(function (tb) {
      var rows = [].slice.call(tb.querySelectorAll("tbody tr"));
      if (rows.length < 2) return;
      var cols = tb.querySelectorAll("thead th").length;
      for (var c = 0; c < cols; c++) {
        var cells = rows.map(function (r) { return r.children[c]; }).filter(Boolean);
        var pairs = cells.map(function (td) { return { td: td, v: numOf(td) }; })
                         .filter(function (p) { return !isNaN(p.v); });
        if (pairs.length < 2) continue;
        var vals = pairs.map(function (p) { return p.v; });
        var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
        if (!(hi > lo)) continue;                                    // 整列一样，画了没意义
        pairs.forEach(function (p) {
          var f = (p.v - lo) / (hi - lo);
          p.td.style.setProperty("--bar", (6 + f * 94).toFixed(1) + "%");
          p.td.classList.add("hasbar");
        });
      }
    });
  }

  /* 代码块加"复制"按钮；正文渲染完调用一次。图片的灯箱走事件委托，不用逐个绑。 */
  function enhanceProse(root) {
    barTables(root);
    root.querySelectorAll("pre.code").forEach(function (pre) {
      if (pre.querySelector(".copy")) return;
      var b = document.createElement("button");
      b.className = "copy";
      b.type = "button";
      b.textContent = i18n.t("common.copy");
      b.addEventListener("click", function () {
        navigator.clipboard.writeText(pre.querySelector("code").textContent).then(function () {
          b.textContent = i18n.t("common.copied");
          setTimeout(function () { b.textContent = i18n.t("common.copy"); }, 1400);
        }, function () { toast(i18n.t("toast.copy.failed"), true); });
      });
      pre.appendChild(b);
    });
  }

  function openLightbox(img) {
    var fig = img.closest("figure");
    $("#lb-img").src = img.currentSrc || img.src;
    $("#lb-img").alt = img.alt || "";
    var cap = (fig && fig.querySelector("figcaption")) ? fig.querySelector("figcaption").textContent : (img.title || img.alt || "");
    $("#lb-cap").textContent = cap;
    $("#lb-cap").hidden = !cap;
    $("#lightbox").hidden = false;
  }
  function closeLightbox() { $("#lightbox").hidden = true; $("#lb-img").src = ""; }

  /* -------------------------------------------------------------- 详情 */

  function crumbs(s) {
    var chain = [], cur = s.id, seen = Object.create(null);
    while (cur && IDX[cur] && !seen[cur]) { seen[cur] = 1; chain.push(cur); cur = IDX[cur].parent; }
    chain.reverse();
    return '<nav class="crumbs">' + chain.map(function (id, i) {
      var last = i === chain.length - 1;
      return (i ? '<span class="sep">›</span>' : "")
        + (last ? "<b>" + esc(id) + "</b>" : '<a href="#' + esc(id) + '" data-goto="' + esc(id) + '">' + esc(id) + "</a>");
    }).join("") + "</nav>";
  }

  /* 徽章文案在 i18n 的 path.kind.*；认不出来的 kind 原样显示（trace_core 加了
     新类型而这边还不认识时，显示裸英文单词好过显示空白）。 */
  function kindLabel(kind) {
    var key = "path.kind." + kind;
    return i18n.has(key) ? i18n.t(key) : String(kind || "");
  }

  /* 角色的显示名。和 kindLabel 同一个套路：词表之外的值原样显示，
     不认识不等于不存在（服务端将来加第五种 role 时界面不该显示成空白）。 */
  function roleLabel(role) {
    var key = "path.role." + role;
    return i18n.has(key) ? i18n.t(key) : String(role || "");
  }

  /* 一条路径上那些机器记下来的事实：大小、条目数、校验和、最后一次核对。
     各占一格而不是挤成一句话——这一整条需求的来历是「三个目录已被删除、
     57 GB 那个，本该自动发现」，而挤成一句话时人正是扫不到那一格。 */
  function pathFacts(p) {
    var out = [];
    if (p.size !== null && p.size !== undefined && p.size !== "") {
      out.push('<span class="pfact mono">' + esc(human(p.size)) + "</span>");
    }
    if (p.n !== null && p.n !== undefined && p.n !== "") {
      out.push('<span class="pfact mono">' + esc(i18n.t("path.n", { n: p.n })) + "</span>");
    }
    if (p.checksum) {
      var algo = String(p.checksum).split(":")[0];
      var val = String(p.checksum).slice(algo.length + 1);
      out.push('<span class="pfact mono" title="' + esc(i18n.t("path.checksum.title", { algo: algo })) + '">'
        + esc(i18n.t("path.checksum", { algo: algo, value: val })) + "</span>");
    }
    // 未知属性照样摆出来。半年后有人写了 nodes=…，界面把它吃掉就等于替他删了记录。
    var known = { size: 1, n: 1, md5: 1, sha256: 1, checked: 1, missing: 1 };
    Object.keys(p.attrs || {}).forEach(function (k) {
      if (known[k]) return;
      out.push('<span class="pfact mono unk" title="'
        + esc(i18n.t("path.attr.unknown.title", { k: k, v: p.attrs[k] })) + '">'
        + esc(k + "=" + p.attrs[k]) + "</span>");
    });
    if (p.state === "missing") {
      out.push('<span class="pfact gone" title="' + esc(i18n.t("path.missing.title", { date: p.missing })) + '">'
        + esc(i18n.t("path.missing", { date: p.missing })) + "</span>");
    } else if (p.state === "present") {
      out.push('<span class="pfact ok" title="' + esc(i18n.t("path.checked.title", { date: p.checked })) + '">'
        + esc(i18n.t("path.checked", { date: p.checked })) + "</span>");
    } else {
      out.push('<span class="pfact none" title="' + esc(i18n.t("path.unchecked.title")) + '">'
        + esc(i18n.t("path.unchecked")) + "</span>");
    }
    return '<div class="pfacts">' + out.join("") + "</div>";
  }

  function pathRow(p) {
    var loc = esc(p.location);
    var isLink = /^https?:\/\//i.test(p.location);
    return '<div class="pathrow' + (p.state === "missing" ? " gone" : "") + '">'
      + '<span class="pkind k-' + esc(p.kind) + '">' + esc(kindLabel(p.kind)) + "</span>"
      + (isLink
          ? '<a class="ploc" href="' + loc + '" target="_blank" rel="noopener noreferrer">' + loc + "</a>"
          : '<code class="ploc">' + loc + "</code>")
      + (p.state === "missing"
          ? '<span class="pgone" title="' + esc(i18n.t("path.missing.title", { date: p.missing })) + '">'
            + esc(i18n.t("path.missing.badge")) + "</span>" : "")
      + '<button class="pcopy" type="button" data-copy="' + loc + '" title="' + esc(i18n.t("common.copy")) + '">⧉</button>'
      + (p.note ? '<span class="pnote">' + esc(p.note) + "</span>" : "")
      + pathFacts(p)
      + "</div>";
  }

  /* 外部产物的位置。checkpoint、数据集这些 GB 级的东西不进仓库，
     只在这里记"它在哪"——溯源时最常问的就是这个。
     按 role 分组：「别人能看到这一步做了什么」本质上就是分清读进来的、跑的、
     写出去的、留作凭据的这四类。没标 role 的旧记录（现存的全部）排在最后，
     一个字不改也照样显示——向后兼容是硬要求。 */
  function renderPaths(s) {
    var ps = s.paths || [];
    if (!ps.length) return "";
    var groups = U.PATH_ROLES.concat([""]).map(function (role) {
      return { role: role, rows: ps.filter(function (p) {
        return role ? p.role === role : U.PATH_ROLES.indexOf(p.role) < 0;
      }) };
    }).filter(function (g) { return g.rows.length; });
    var gone = ps.filter(function (p) { return p.state === "missing"; }).length;
    var head = gone
      ? '<p class="pathsum">' + esc(i18n.t("path.summary.missing", { n: gone })) + "</p>" : "";
    // 一个组也分不出来（全是没标 role 的老记录）时不加分组标题：那只是噪声
    var single = groups.length === 1 && !groups[0].role;
    return '<div class="pathbox">' + head + groups.map(function (g) {
      var title = g.role
        ? '<h4 class="prole" title="' + esc(i18n.t("path.role." + g.role + ".title")) + '">'
          + esc(roleLabel(g.role)) + "</h4>"
        : (single ? "" : '<h4 class="prole quiet">' + esc(i18n.t("path.role.none")) + "</h4>");
      return title + g.rows.map(pathRow).join("");
    }).join("") + "</div>";
  }

  /* ⑤ 代码在哪。`commit:` 只是其中一种写法——代码不在 git 里的时候
     （超算上直接改脚本，跑完打一个快照目录 + 逐文件校验和）同样答得了
     「代码在哪」，凭什么永远停在 L1。 */
  function renderCode(s) {
    var cs = s.code || [];
    if (!cs.length) return "";
    return '<div class="sec codebox"><h3>' + esc(i18n.t("code.head", { n: cs.length })) + "</h3>"
      + cs.map(function (c) {
          var kindKey = "code.kind." + c.kind;
          var label = i18n.has(kindKey) ? i18n.t(kindKey) : String(c.kind || "");
          var tip = i18n.has(kindKey + ".title") ? i18n.t(kindKey + ".title") : "";
          var attrs = c.attrs || {};
          var facts = [];
          if (attrs.manifest) {
            facts.push('<span class="pfact mono" title="' + esc(i18n.t("code.manifest.title")) + '">'
              + esc(i18n.t("code.manifest", { name: attrs.manifest })) + "</span>");
          }
          if (attrs.n) facts.push('<span class="pfact mono">' + esc(i18n.t("code.files", { n: attrs.n })) + "</span>");
          Object.keys(attrs).forEach(function (k) {
            if (k === "manifest" || k === "n") return;
            facts.push('<span class="pfact mono">' + esc(k + "=" + attrs[k]) + "</span>");
          });
          var loc = String(c.location || "");
          var isLink = /^https?:\/\//i.test(loc);
          return '<div class="coderow">'
            + '<span class="ckind" title="' + esc(tip) + '">' + esc(label) + "</span>"
            + (loc
                ? (isLink
                    ? '<a class="ploc" href="' + esc(loc) + '" target="_blank" rel="noopener noreferrer">' + esc(loc) + "</a>"
                    : '<code class="ploc">' + esc(loc) + "</code>")
                : "")
            // 派生出来的那条要标出来：文件里只有一行 `commit:`，没有第二份。
            + (c.from === "commit"
                ? '<span class="cderived" title="' + esc(i18n.t("code.from.commit.title")) + '">'
                  + esc(i18n.t("code.from.commit")) + "</span>" : "")
            + (c.note ? '<span class="pnote">' + esc(c.note) + "</span>" : "")
            + (facts.length ? '<div class="pfacts">' + facts.join("") + "</div>" : "")
            + "</div>";
        }).join("") + "</div>";
  }

  function stepLink(id) {
    return '<a href="#' + esc(id) + '" data-goto="' + esc(id) + '">' + esc(id) + "</a>";
  }

  /* ② 数据依赖，两个方向都要有。
   *
   * 上游（本步消费了谁的产物）是文件里写着的 `input:`；下游（谁消费了本步的）
   * 是扫全项目现算出来的反向边，绝不存储——存了就是第二份真相。
   *
   * 顶上那句 input.lead 是这个区块存在的全部意义：`parent` 是我当时接着哪一步想，
   * `input` 是这些字节从哪来。少了它，读者只会把 input 当成「第二个 parent」。
   */
  function renderDeps(s) {
    var ins = s.inputs || [], outs = s.consumers || [];
    if (!ins.length && !outs.length) return "";
    var up = ins.length
      ? '<ul class="deplist">' + ins.map(function (i) {
          var known = !!IDX[i.step];
          var link = known ? stepLink(i.step) : '<code>' + esc(i.step) + "</code>";
          var line = i.note
            ? i18n.tHtml("input.entry", { link: { html: link }, what: i.note })
            : i18n.tHtml("input.entry.bare", { link: { html: link } });
          return '<li' + (known ? "" : ' class="dangling"') + ">" + line
            + (known ? '<span class="deptitle">' + esc(titleOf(i.step)) + "</span>"
                     : '<span class="depwarn">' + esc(i18n.t("input.warn.missing", { id: i.step })) + "</span>")
            + "</li>";
        }).join("") + "</ul>"
      : '<p class="dropnote">' + esc(i18n.t("input.empty")) + "</p>";
    var down = outs.length
      ? '<ul class="deplist">' + outs.map(function (id) {
          return "<li>" + stepLink(id) + '<span class="deptitle">' + esc(titleOf(id)) + "</span></li>";
        }).join("") + "</ul>"
      : '<p class="dropnote">' + esc(i18n.t("input.consumers.empty")) + "</p>";
    return '<div class="sec depbox"><h3>' + esc(i18n.t("input.head", { n: ins.length })) + "</h3>"
      + '<p class="dropnote deplead">' + i18n.tHtml("input.lead") + "</p>"
      + up
      + '<h4 class="rhead">' + esc(i18n.t("input.consumers.head", { n: outs.length })) + "</h4>"
      + down + "</div>";
  }

  /* ① 移动审计。顺序即历史，只追加。
   *
   * 为什么要显示它：用户吃过的亏正是「创建日期和内容对不上号」，而一棵移动过的树
   * 本来就会和创建顺序对不上——不把这几行摆出来，那种对不上就会被当成 bug。 */
  function renderMoved(s) {
    var ms = s.moved || [];
    if (!ms.length) return "";
    return '<div class="sec movedbox"><h3>' + esc(i18n.t("move.head", { n: ms.length })) + "</h3>"
      + ms.map(function (m) {
          var vars = {
            date: m.date || "",
            from: m.from ? m.from : i18n.t("move.from.root"),
            to: m.to ? m.to : i18n.t("move.to.root"),
            reason: m.reason || "",
            by: m.by === "human" ? i18n.t("move.by.human") : (m.by || ""),
          };
          return '<div class="moverow">'
            + esc(i18n.t(m.by ? "move.entry" : "move.entry.nobody", vars)) + "</div>";
        }).join("") + "</div>";
  }

  /* ---------------------------------------------------------- 可溯源性 */

  function levelName(l) { return i18n.t("trace.level." + l); }
  function reproName(st) { return i18n.t("trace.repro." + st); }

  function lvChip(level, extra) {
    var l = U.LEVELS.indexOf(level) >= 0 ? level : "L0";
    return '<span class="lv lv-' + l + (extra ? " " + extra : "") + '" title="'
      + esc(i18n.t("trace.chip.title", { level: l, name: levelName(l), hint: i18n.t("trace.level." + l + ".hint") })) + '">'
      + l + " " + esc(levelName(l)) + "</span>";
  }

  /* FORMAT.md 第 10 节算出来的等级，人这一侧的出口。
   *
   * 三件事必须同时在场，少一件这块就没用：
   *  1) 自身等级——这一步写得够不够；
   *  2) 整条链的等级 + 最弱的那一环是谁（可点过去）——「补记录要从最弱的一环补起，
   *     不是从最新那一步补起」，不指出是谁，这句话就落不了地；
   *  3) missing 是一份可执行的 todo，不是评语。
   * repro 记录连 failed 的一起列——「试过，checkpoint 被清了，跑不了」本身就是
   * 溯源结论，把它藏起来等于把最贵的那条信息丢掉。
   */
  function renderTrace(s) {
    var t = s.trace;
    var repro = s.repro || [];
    if (!t) return "";

    var weak = t.weakest && t.weakest !== s.id ? IDX[t.weakest] : null;
    var head = '<div class="lvrow">' + lvChip(t.self)
      + '<span class="lvcap">' + esc(i18n.t("trace.self")) + "</span>"
      + '<span class="lvsep">·</span>' + lvChip(t.chain, "chain")
      + '<span class="lvcap">' + esc(i18n.t("trace.chain")) + "</span>";
    if (weak) {
      head += '<span class="lvsep">—</span><span class="lvcap">'
        + i18n.tHtml("trace.weakest", {
            link: { html: '<a href="#' + esc(weak.id) + '" data-goto="' + esc(weak.id) + '">' + esc(weak.id) + "</a>" },
            title: stepTitle(weak),
          })
        + "</span>";
    } else if (t.weakest === s.id && t.chain !== "L4") {
      head += '<span class="lvsep">—</span><span class="lvcap">' + esc(i18n.t("trace.weakest.self")) + "</span>";
    }
    /* via 说的是最弱一环是**从哪条边**找过去的。没有它，「整链等级比面包屑里
       任何一环都低」看着就像算错了——其实是最弱的那一环挂在数据依赖上，
       而 lineage 只画得出 parent 那条路（DAG 摊不成面包屑）。 */
    if (weak && t.via === "input") {
      head += '<span class="viatag" title="' + esc(i18n.t("input.parent.tip")) + '">'
        + esc(i18n.t("flow.legend.data")) + "</span>";
    }
    head += "</div>";

    // missing 的每一条都是服务端算出来的中文（Python 侧不在翻译范围），
    // 过一遍 traceMissing 换成本语言的说法；认不出的原样显示，不吞。
    var todo = (t.missing || []).length
      ? '<ul class="lvmiss">' + t.missing.map(function (m) {
          return "<li>" + esc(i18n.traceMissing(m)) + "</li>";
        }).join("") + "</ul>"
      : '<p class="dropnote lvok">' + esc(i18n.t("trace.ok")) + "</p>";

    var chain = (t.lineage || []).length > 1
      ? '<div class="lvchain">' + t.lineage.map(function (e) {
          return '<a class="lvnode lv-' + esc(e.level) + (e.id === s.id ? " here" : "")
            + '" href="#' + esc(e.id) + '" data-goto="' + esc(e.id) + '" title="'
            + esc(titleOf(e.id)) + '">' + esc(e.id)
            + '<i>' + esc(e.level) + "</i></a>";
        }).join('<span class="lvarrow">→</span>') + "</div>"
      : "";

    var rp = repro.length
      ? '<div class="repros">' + repro.map(function (r) {
          var st = U.REPRO_STATES.indexOf(r.state) >= 0 ? r.state : "unknown";
          return '<div class="repro r-' + st + '">'
            + '<span class="rstate">' + esc(reproName(st)) + "</span>"
            + (r.date ? '<span class="rmeta">' + esc(r.date) + "</span>" : "")
            + (r.by ? '<span class="rmeta">' + esc(r.by) + "</span>" : "")
            + (r.note ? '<span class="rnote">' + esc(r.note) + "</span>" : "")
            + "</div>";
        }).join("") + "</div>"
      : '<p class="dropnote">' + i18n.tHtml("trace.repro.empty") + "</p>";

    /* L2 的判据放宽之后要在这里说一句：快照目录 + 逐文件校验和不比 commit 差。
       只在这一步真的靠非 git 的方式定位到代码时才说——对一个普通的 git 记录
       讲这段话是噪声。 */
    var l2 = (s.code || []).some(function (c) { return c.kind && c.kind !== "git"; })
      ? '<p class="dropnote l2note">' + i18n.tHtml("code.l2.note") + "</p>" : "";

    return '<div class="sec tracebox"><h3>' + esc(i18n.t("trace.title")) + "</h3>"
      + head + todo + l2 + chain
      + '<h4 class="rhead">' + esc(i18n.t("trace.repro.head", { n: repro.length })) + "</h4>" + rp + "</div>";
  }

  /* 编辑框里的三块结构化文本。**必须**走 U.format*（逐字对着 trace_core 的
     format_path / format_input / format_code），否则一次无关的编辑——改个标题、
     改个状态——就会把 role、校验和、最后核对日期整组抹掉。 */
  function pathsToText(s) {
    return (s.paths || []).map(U.formatPath).join("\n");
  }
  function inputsToText(s) {
    return (s.inputs || []).map(U.formatInput).join("\n");
  }
  function codeToText(s) {
    // from == "commit" 的那条是**派生**的（由 `commit:` 折算出来）。摆进框里
    // 就等于让人把同一个事实写第二遍，而且它没有位置，原样发回去写入侧会拒。
    return (s.code || []).filter(function (c) { return c.from !== "commit"; })
      .map(U.formatCode).join("\n");
  }
  function textToPaths(text) {
    return String(text || "").split("\n").map(function (l) { return l.trim(); }).filter(Boolean);
  }

  /* 项目洞察：不属于任何单独一步的沉淀——核心想法、什么有效什么无效、
     踩过的坑。存在 project.md 的正文里，所以照样可 grep、可 diff。
     没选步骤时详情面板就显示它，这也是打开项目时的落地页。 */
  /* 四种洞察的语义键。按钮上的字和提示走 i18n（insight.<k> / insight.<k>.hint），
     而它们**逐字对着 trace_core.INSIGHT_NAMES**（i18n.test.js 钉着这一条），
     所以「＋ 有效」这个按钮说的词和最终落进 project.md 的小节名一定是同一个。 */
  var INSIGHT_KINDS = ["idea", "works", "fails", "pitfall"];
  function insightLabel(k) { return i18n.t("insight." + k); }

  function currentProject() {
    for (var i = 0; i < PROJECTS.length; i++) if (PROJECTS[i].slug === PROJECT) return PROJECTS[i];
    return null;
  }

  function keyRow(k, label) {
    return '<span class="mono">' + k + "</span> " + esc(i18n.t(label));
  }

  /* ---------------------------------------------------------- ④ 洞察逐条渲染
   *
   * 一条洞察在磁盘上就是 project.md 里的一行 `- `。这里把它按条摆出来，
   * 为的是三件事：
   *   · id 是个把手——「见 p3」这样的引用要一直有效，所以 id 得看得见；
   *   · 「取代了 p1」只写在取代者身上，「p1 已被取代」是**派生**的，
   *     所以那半句话由这里算出来显示，磁盘上一个字都不多写；
   *   · 被取代的那条默认折叠但**绝不删除**——你当初信的那件事是你走到今天的
   *     一部分，删了它，后来的更正看着就像凭空冒出来的。
   */
  function insightBody() {
    var p = currentProject() || { body: "" };
    return String(p.body || "");
  }
  function findInsight(iid) {
    var items = U.parseInsights(insightBody());
    var found = null;
    Object.keys(items).forEach(function (k) {
      items[k].forEach(function (it) { if (!found && it.id === iid) found = { kind: k, item: it }; });
    });
    return found;
  }

  function insightRow(it, kind, byId, editable) {
    var bits = '<span class="insid" title="' + esc(i18n.t("insight.id.title")) + '">'
      + esc(i18n.t("insight.id", { id: it.id })) + "</span>";
    var text = '<div class="instext">' + window.md.render(it.text, { resolve: function (h) { return h; } }) + "</div>";
    var tags = "";
    it.supersedes.forEach(function (t) {
      tags += byId[t]
        ? '<span class="instag">' + esc(i18n.t("insight.supersedes", { id: t })) + "</span>"
        : '<span class="instag warn">' + esc(i18n.t("insight.warn.missing", { id: t })) + "</span>";
    });
    it.superseded_by.forEach(function (t) {
      tags += '<span class="instag old" title="' + esc(i18n.t("insight.superseded.title")) + '">'
        + esc(i18n.t("insight.superseded", { id: t })) + "</span>";
    });
    var acts = (editable && it.id)
      ? '<span class="insacts">'
        + '<button data-ins-edit="' + esc(it.id) + '">' + esc(i18n.t("insight.item.edit")) + "</button>"
        + '<button data-ins-sup="' + esc(it.id) + '" title="' + esc(i18n.t("insight.supersede.hint")) + '">'
        + esc(i18n.t("insight.supersede.act")) + "</button></span>"
      : "";
    return '<li class="insrow' + (it.superseded_by.length ? " isold" : "") + '">'
      + (it.id ? bits : "") + text + tags + acts + "</li>";
  }

  function renderInsightBody(body, editable) {
    var secs = U.splitSections(body);
    var items = U.parseInsights(body);
    var byId = Object.create(null);
    Object.keys(items).forEach(function (k) {
      items[k].forEach(function (it) { if (it.id && !byId[it.id]) byId[it.id] = it; });
    });
    var done = Object.create(null), out = "";
    secs.forEach(function (sec) {
      var text = sec.lines.join("\n").replace(/^\n+|\n+$/g, "");
      if (!text.trim()) return;
      var kind = sec.heading === null ? null : U.INSIGHT_KIND_BY_HEADING[sec.heading];
      // 洞察之外的小节（尤其「## 已删除」）原样渲染：那几行是步骤被真删之后
      // 唯一还 grep 得到的证据，一个字都不能少。
      if (!kind || done[kind]) {
        out += '<div class="prose">' + window.md.render(text, { resolve: function (h) { return h; } }) + "</div>";
        return;
      }
      done[kind] = 1;
      var live = items[kind].filter(function (it) { return !it.superseded_by.length; });
      var old = items[kind].filter(function (it) { return it.superseded_by.length; });
      out += '<div class="inssec"><h2>' + esc(sec.heading) + "</h2>"
        + (live.length
            ? '<ul class="inslist">' + live.map(function (it) {
                return insightRow(it, kind, byId, editable);
              }).join("") + "</ul>"
            : "")
        + (old.length
            ? '<button class="insfold" data-ins-fold="' + esc(kind) + '">'
              + esc(i18n.t("insight.superseded.show", { n: old.length })) + "</button>"
              + '<ul class="inslist insold" data-old="' + esc(kind) + '" hidden>'
              + old.map(function (it) { return insightRow(it, kind, byId, editable); }).join("")
              + "</ul>"
            : "")
        + "</div>";
    });
    return out;
  }

  function renderInsights(el) {
    var p = currentProject() || { name: PROJECT, body: "" };
    // 洞察正文同样跟语言走：project.en.md 里的四个小节是同一批判断的英文那一份。
    var pick = U.pickLang(p, uiLang());
    var body = String((pick.tr && pick.tr.body) || p.body || "").trim();
    var acts = canWrite()
      ? '<div class="acts"><button data-act="edit-insights">' + esc(i18n.t("insight.edit")) + "</button>"
        + INSIGHT_KINDS.map(function (k) {
            return '<button data-add-insight="' + k + '" title="' + esc(i18n.t("insight." + k + ".hint")) + '">'
              + esc(i18n.t("insight.add", { label: insightLabel(k) })) + "</button>";
          }).join("")
        + '<span class="sp"></span><button data-act="child" class="primary">'
        + esc(i18n.t("app.new")) + "</button></div>"
      : "";
    var content = body
      ? trNotice(p, "tr.fallback.project")
        // 逐条渲染而不是整段 md.render：④ 要的「被取代的折叠起来、取代者标出
        // 取代了谁」是**每一条**上的事，整段渲染时它们只是四行看不出关系的 bullet。
        // 能不能改由「此刻显示的是不是原文」决定：写入只会落到 project.md，
        // 对着译文点「编辑这一条」会改到另一个文件上去。
        + renderInsightBody(body, canWrite() && !pick.tr)
      : '<p class="dropnote">' + i18n.tHtml("insight.empty") + "</p>";

    el.innerHTML = '<div class="insights">'
      + '<h1 class="title">' + esc(i18n.t("insight.title", { name: projectName(p) || PROJECT })) + "</h1>"
      + '<p class="dropnote">' + i18n.tHtml("insight.lead") + "</p>"
      + acts + content + "</div>"
      + '<div class="sec"><h3>' + esc(i18n.t("keys.title")) + '</h3><p class="dropnote">'
      + keyRow("↑ ↓", "keys.move") + " · " + keyRow("g", "keys.toggle") + " · "
      + keyRow("n", "keys.new") + " · " + keyRow("e", "keys.edit") + " · "
      + keyRow("/", "keys.search") + " · " + keyRow("Esc", "keys.back")
      + "</p></div>";
    enhanceProse(el);
    el.scrollTop = 0;
  }

  function patchProject(payload) {
    return api("/api/projects/" + encodeURIComponent(PROJECT), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      // 响应要一路带回调用处：新写的那条洞察分到的 id 只有服务端知道，
      // 而不知道它叫 p3，人就说不出下一句「· 取代 p3」。
      return refreshProjects().then(function () { onHash(); return r || {}; });
    });
  }

  /* 「编辑洞察」只提交它真正编辑的那部分。
   *
   * 从前这个框预填的是 project.md 的**整段正文**，保存时也整段提交回去。两件事叠在
   * 一起就会静默毁记录：(a) 预填用的是打开页面那一刻的旧值，这期间 agent 通过
   * trace_insight 记的东西不在框里；(b) 整段里还包含「## 已删除」——步骤目录被真删
   * 之后，那几行「为什么删的」是 G4 唯一还能 grep 到的证据。一次保存两样一起没。
   *
   * 内核侧现在有兜底（update_project 只替换四个洞察小节），但前端不能靠兜底：
   * 兜底只保证磁盘不坏，不保证人看到的是真相——框里显示着「## 已删除」，用户改了它
   * 却发现改不动，那是另一种形式的骗人。所以这里把正文切开：洞察进可编辑框，
   * 其余小节以只读形式列在下面并写明「这次编辑不会动它」。
   */
  function openInsightEditor() {
    // 预填之前先取一次最新的：这个框最大的风险就是拿旧正文覆盖新内容。
    var go = MODE === "server" ? refreshProjects() : Promise.resolve();
    go.then(function () {
      var p = currentProject() || { body: "" };
      var split = U.splitInsightBody(p.body || "");
      var others = split.others.length
        ? '<div class="others"><h4>' + esc(i18n.t("insight.editor.others")) + "</h4>"
          + split.others.map(function (o) {
              return '<pre class="othersec">' + esc(o.text) + "</pre>";
            }).join("")
          + '<p class="dropnote">' + i18n.tHtml("insight.editor.others.note") + "</p></div>"
        : "";
      $("#detail").innerHTML =
        '<div class="edhead"><b>' + esc(i18n.t("insight.editor.title")) + "</b>"
        // 这个框改的永远是 project.md 本身（PATCH /api/projects 只写原文）。
        // 界面切成英文、上面显示的是 project.en.md 时尤其要说清这一点，
        // 否则人以为自己在改英文版，保存后发现英文版一个字没变。
        + '<span class="trbadge" title="' + esc(i18n.t("tr.fallback.project")) + '">'
        + esc(i18n.t("tr.badge.original")) + "</span>"
        + '<span class="sp"></span>'
        + '<button data-act="save-insights" class="primary">' + esc(i18n.t("editor.save")) + " <kbd>Ctrl↵</kbd></button>"
        + '<button data-act="cancel">' + esc(i18n.t("editor.cancel")) + "</button></div>"
        + '<textarea class="editor" id="ed-insights" spellcheck="false" style="min-height:360px">'
        + esc(split.editable)
        + "</textarea>"
        + '<p class="dropnote" id="ed-insights-warn"></p>'
        + '<p class="dropnote">' + i18n.tHtml("insight.editor.hint") + "</p>"
        + others;
      var ta = $("#ed-insights");
      ta.addEventListener("input", paintInsightWarn);
      paintInsightWarn();
      ta.focus();
    }).catch(fail);
  }

  function paintInsightWarn() {
    var ta = $("#ed-insights"), box = $("#ed-insights-warn");
    if (!ta || !box) return;
    var bad = U.foreignHeadings(ta.value);
    box.innerHTML = bad.length
      ? i18n.tHtml("insight.editor.warn", {
          sections: bad.map(function (h) { return "## " + h; }).join(" · "),
        })
      : "";
    box.classList.toggle("warn", !!bad.length);
  }

  function saveInsights() {
    var ta = $("#ed-insights");
    if (!ta) return;
    patchProject({ insights: ta.value })
      .then(function () { toast(i18n.t("toast.insights.saved")); }).catch(fail);
  }

  function renderDetail() {
    var el = $("#detail"), s = IDX[selected()];
    document.body.classList.toggle("editing", !!(editing && s));
    if (!s) { renderInsights(el); return; }   // 没选步骤时，详情面板就是项目主页
    if (editing) return renderEditor(s);

    var meta = ['<span class="pill s-' + s.status + '">' + s.status + "</span>"];
    // 整条链的等级放在最显眼处：人常常只看一眼顶部就走，而「这个结论追不追得到底」
    // 恰恰是最该被那一眼看到的。详细的缺项和复现记录在下面的可溯源性小节里。
    if (s.trace) meta.push(lvChip(s.trace.chain, "mini"));
    if (s.date) meta.push(esc(s.date));
    if (s.commit) meta.push(esc(i18n.t("detail.meta.commit", { commit: s.commit })));
    // 非 git 的代码位置也上 meta：⑤ 之后「代码在哪」不只有 commit 一种答案
    (s.code || []).forEach(function (c) {
      if (c.from === "commit" || !c.location) return;
      meta.push(esc(i18n.t("detail.meta.code", { kind: c.kind, loc: c.location })));
    });
    if (s.author) meta.push(esc(s.author));
    if (s.parent) {
      meta.push('<span title="' + esc(i18n.t("input.parent.tip")) + '">'
        + i18n.tHtml("detail.meta.parent", {
            id: { html: '<a href="#' + esc(s.parent) + '" data-goto="' + esc(s.parent) + '">' + esc(s.parent) + "</a>" },
          }) + "</span>");
    }
    // 数据依赖只在它**和 parent 说的不是同一件事**时上 meta：树上那一条已经
    // 写在旁边了，重复一遍只会让人以为 input 就是第二个 parent。
    var offTree = (s.inputs || []).filter(function (i) { return i.step !== s.parent; });
    if (offTree.length) {
      meta.push('<span class="depchip" title="' + esc(i18n.t("input.parent.tip")) + '">'
        + esc(i18n.t("detail.meta.inputs", { ids: offTree.map(function (i) { return i.step; }).join(" · ") }))
        + "</span>");
    }
    if ((s.moved || []).length) {
      meta.push('<span class="movechip" title="'
        + esc(i18n.t("move.badge.title", { n: s.moved.length })) + '">'
        + esc(i18n.t("move.badge")) + "</span>");
    }
    if ((s.children || []).length) meta.push(esc(i18n.t("count.children", { n: s.children.length })));
    (s.tags || []).forEach(function (tag) { meta.push('<span class="tag">' + esc(tag) + "</span>"); });

    var acts = "";
    if (canWrite()) {
      acts = '<div class="acts">'
        + '<button data-act="edit">' + esc(i18n.t("detail.act.edit")) + "</button>"
        + ["wip", "done", "dead"].map(function (st) {
            return '<button data-status="' + st + '"' + (s.status === st ? ' class="on"' : "") + ">" + st + "</button>";
          }).join("")
        // 「移动」不是主操作，但它必须在场：没有它，人只能回去用「把正文对调」
        // 那种老办法，而那种办法是不留痕迹的。
        + '<button data-act="move" title="' + esc(i18n.t("move.act.title")) + '">'
        + esc(i18n.t("move.act")) + "</button>"
        + '<span class="sp"></span>'
        + '<button data-act="delete" class="danger" title="' + esc(i18n.t("detail.act.delete.title")) + '">'
        + esc(i18n.t("detail.act.delete")) + "</button>"
        + '<button data-act="child" class="primary">' + esc(i18n.t("detail.act.child")) + "</button>"
        + "</div>";
    }

    var paths = renderPaths(s);
    var body = window.md.render(stepBody(s), { resolve: resolverFor(s) });

    var back = "";
    if ((s.backlinks || []).length) {
      back = '<div class="sec"><h3>' + esc(i18n.t("detail.backlinks")) + '</h3><div class="crumbs">'
        + s.backlinks.map(function (id) {
            return '<a href="#' + esc(id) + '" data-goto="' + esc(id) + '">' + esc(id) + "</a> "
              + esc(titleOf(id));
          }).join("<br>")
        + "</div></div>";
    }

    var files = '<div class="sec"><h3>'
      + esc(i18n.t("detail.files", { n: (s.files || []).length })) + "</h3>";
    if ((s.files || []).length) {
      files += '<div class="files">' + s.files.map(function (f) {
        var url = fileURL(s, f.path);
        var thumb = IMG.test(f.path)
          ? '<img class="thumb zoomable" src="' + url + '" alt="' + esc(f.path) + '" loading="lazy">' : "";
        return '<div class="file">' + thumb + '<a href="' + url + '" target="_blank" rel="noopener">' + esc(f.path) + "</a>"
          + '<div class="sz">' + esc(human(f.size))
          + (canWrite() ? ' · <a href="#" data-rm="' + esc(f.path) + '">' + esc(i18n.t("detail.file.remove")) + "</a>" : "")
          + "</div></div>";
      }).join("") + "</div>";
    } else {
      files += '<p class="dropnote">' + esc(i18n.t("detail.files.empty")) + "</p>";
    }
    if (canWrite()) files += '<p class="dropnote">' + i18n.tHtml("detail.files.drop") + "</p>";
    files += "</div>";

    el.innerHTML = crumbs(s)
      + '<h1 class="title">' + esc(stepTitle(s) || i18n.t("common.untitled")) + "</h1>"
      + '<div class="meta">' + meta.join("") + "</div>" + acts + paths + renderCode(s)
      + trNotice(s)
      + '<div class="prose">' + body + "</div>" + renderDeps(s) + back
      + renderMoved(s) + renderTrace(s) + files;
    enhanceProse(el);
    el.scrollTop = 0;
  }

  /* -------------------------------------------------------------- 草稿 */

  /* 正文是人一个字一个字想出来的，丢了就是丢了。所以编辑器里的内容一边写一边落进
     localStorage，退出、刷新、误点、断网重试全都不清它——只有「保存成功」和用户
     明确点「丢弃」这两件事清。
     恢复不自动做：直接把草稿盖上去等于替用户做了选择，而磁盘上那一份可能才是新的
     （别人/agent 刚改过）。所以下次打开时把两边都摆出来，让人挑。 */
  var DRAFT_DEBOUNCE = 500;
  var draftTimer = null;
  var NEW_DRAFT_ID = "__new__";   // 新建对话框那份草稿的键；真实 id 只可能是数字或 00X~dupN

  /* 中英两份正文各存各的草稿：共用一个键就是「写完中文切去写英文，
     回来发现中文稿被英文稿盖了」。edLang 为空表示编辑的是 note.md（原文），
     此时草稿键和从前逐字一样，升级前写到一半的草稿不会变成孤儿。 */
  function readDraft(id, l) {
    try {
      var raw = localStorage.getItem(U.draftKey(PROJECT, id, l || ""));
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function writeDraft(id, d, l) {
    // 配额满 / 隐私模式下写不进去：草稿是加成，不该反过来拦住人写正文
    try { localStorage.setItem(U.draftKey(PROJECT, id, l || ""), JSON.stringify(d)); } catch (e) { /* 忽略 */ }
  }
  function dropDraft(id, l) {
    try { localStorage.removeItem(U.draftKey(PROJECT, id, l || "")); } catch (e) { /* 忽略 */ }
  }

  function editorState() {
    var b = $("#ed-body");
    if (!b) return null;
    return { title: ($("#ed-title") || {}).value || "", body: b.value,
             paths: ($("#ed-paths") || {}).value || "",
             inputs: ($("#ed-inputs") || {}).value || "",
             code: ($("#ed-code") || {}).value || "", lang: edLang };
  }
  /* 编辑器此刻对着的那一份磁盘内容。译文只有标题和正文——path / input / code
     都是结构信息，翻译文件里一行都不许有（写两份就是双真相源，而且 core 会
     把它们读都不读地丢掉并报一条 translation_structural_key）。 */
  function editTarget(s, l) {
    if (!s) return { title: "", body: "", paths: "", inputs: "", code: "" };
    if (!l) {
      return { title: s.title || "", body: s.body || "", paths: pathsToText(s),
               inputs: inputsToText(s), code: codeToText(s) };
    }
    var e = (s.tr || {})[l] || {};
    return { title: e.title || "", body: e.body || "", paths: "", inputs: "", code: "" };
  }
  function sameAsStep(s, st) {
    if (!s || !st) return false;
    var base = editTarget(s, st.lang || "");
    // 老草稿里没有 inputs / code 两个键：undefined 当成空串比，
    // 否则升级之后每一份旧草稿都会被判成「有未保存改动」，每次进出都弹框。
    return st.title === base.title && st.body === base.body && st.paths === base.paths
      && (st.inputs || "") === base.inputs && (st.code || "") === base.code;
  }
  function isDirty() {
    if (!editing) return false;
    var s = IDX[selected()], st = editorState();
    return !!(s && st && !sameAsStep(s, st));
  }
  function hhmm() {
    var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }
  function saveDraftNow() {
    var s = IDX[selected()], st = editorState();
    if (!editing || !s || !st) return;
    if (sameAsStep(s, st)) { dropDraft(s.id, st.lang); setEdStatus(""); return; }
    st.at = Date.now();
    // 草稿是基于哪一版写的，恢复时要拿它对账。译文那一侧对的是译文自己的
    // digest（trDigest），和 note.md 的 digest 是两条独立的链。
    st.base = st.lang ? (trDigest[trKey(s.id, st.lang)] || "") : (s.digest || "");
    writeDraft(s.id, st, st.lang);
    setEdStatus(i18n.t("editor.status.draftSaved", { time: hhmm() }));
  }
  function scheduleDraft() {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraftNow, DRAFT_DEBOUNCE);
  }
  function setEdStatus(t) { var e = $("#ed-status"); if (e) e.textContent = t; }

  /* 离开确认。三个出口，因为「留下 / 走」两个不够：用户真正想说的第三件事是
     「这段我不要了，别再拿草稿烦我」。只给两个选项会逼人把不想要的东西一直留着。 */
  var pendingLeave = null;
  function guardLeave(next) {
    if (!isDirty()) { next(); return; }
    saveDraftNow();
    pendingLeave = next;
    var s = IDX[selected()];
    // 只有 id、标题和正在编辑的文件名——三样都是标点无关的，不用为它造一条文案
    $("#leave-what").textContent = s
      ? s.id + " · " + stepTitle(s) + (edLang ? "  (" + noteFileName(edLang) + ")" : "") : "";
    $("#dlg-leave").showModal();
  }
  function resolveLeave(how) {
    var dlg = $("#dlg-leave");
    if (dlg.open) dlg.close();
    var next = pendingLeave;
    pendingLeave = null;
    if (how === "stay") return;
    if (how === "discard") {
      var s = IDX[selected()];
      if (s) dropDraft(s.id, edLang);
      toast(i18n.t("toast.draft.discarded"));
    }
    if (next) next();
  }

  /* -------------------------------------------------------------- 冲突 */

  /* 服务端在 PATCH 冲突时回 409。绝不静默覆盖，也绝不静默丢弃——两边都是人写的字，
     该由人来判。所以把服务器当前的正文和自己编辑中的正文并排摆出来。 */
  function lineSet(t) {
    var m = Object.create(null);
    String(t || "").split("\n").forEach(function (l) { m[l.trim()] = 1; });
    return m;
  }
  function diffPane(text, otherSet) {
    return String(text || "").split("\n").map(function (l) {
      var same = otherSet[l.trim()] || !l.trim();
      return '<div class="dl' + (same ? "" : " ch") + '">' + (esc(l) || "&nbsp;") + "</div>";
    }).join("");
  }

  function handleConflict(s, st, err) {
    // 服务端如果连当前内容一起返回了就直接用；没有就自己再读一次那一步。
    var d = err.data || {};
    var given = d.current || d.server || d.step || null;
    if (st.lang) {
      /* 译文的 409。服务端回的 current 是那一份译文（{title, body, digest}）；
         老服务端可能只回一句话，那就没有两份可比，照常把原话报出来——
         这里绝不去读 note.md 冒充「服务器版本」，那会让人对着不相干的两段文字选。 */
      var mineBase = trDigest[trKey(s.id, st.lang)] || "";
      if (!given || !given.digest || (mineBase && given.digest === mineBase)) { fail(err); return Promise.resolve(); }
      openConflict(s, st, given, err);
      return Promise.resolve();
    }
    var got = given ? Promise.resolve(given) : papi("/steps/" + encodeURIComponent(s.id));
    return got.then(function (server) {
      if (!server || !server.digest || (s.digest && server.digest === s.digest)) {
        // 摘要没变 → 这个 409 不是「别人先改了」（比如重复 id 的 ~dup 标记也会 409）。
        // 那种情况没有两个版本可比，照常把服务端的原话报出来。
        fail(err);
        return;
      }
      openConflict(s, st, server, err);
    }).catch(function () { fail(err); });
  }

  var conflictCtx = null;
  function openConflict(s, mine, server, err) {
    conflictCtx = { id: s.id, lang: mine.lang || "", mine: mine, server: server };
    var a = lineSet(server.body), b = lineSet(mine.body);
    // 服务端那句话是中文（Python 侧不在翻译范围）；它带着 id 和摘要，比一句
    // 泛泛的「变了」有用，所以有就用它，没有才退到本地文案。
    $("#cf-why").textContent = String((err && err.message) || i18n.t("conflict.why"));
    $("#cf-server-meta").textContent = i18n.t("conflict.server.meta", {
      author: server.author || "?", date: server.date || "", digest: server.digest || "",
    });
    $("#cf-mine-meta").textContent = i18n.t("conflict.mine.meta");
    $("#cf-server").innerHTML = diffPane(server.body, b);
    $("#cf-mine").innerHTML = diffPane(mine.body, a);
    $("#dlg-conflict").showModal();
  }

  function resolveConflict(how) {
    var c = conflictCtx;
    var dlg = $("#dlg-conflict");
    if (dlg.open) dlg.close();
    if (!c) return;
    if (how === "cancel") return;                     // 回编辑器，草稿还在
    if (how === "theirs") {
      // 保留服务器版本。自己的那份不丢——它已经在草稿里，下次进这一步会被问要不要恢复。
      conflictCtx = null;
      editing = false;
      refresh().then(function () {
        toast(i18n.t("toast.conflict.theirs"));
      }).catch(fail);
      return;
    }
    // 用我的覆盖：expect 换成刚读到的那一版，这样只覆盖「我看过的这一版」，
    // 而不是变成一个永远不检查冲突的强制写。译文那一侧同理，只是 expect 对的是
    // 译文自己的 digest。
    var go = c.lang
      ? putTranslation(c.id, c.lang, { title: c.mine.title, body: c.mine.body,
                                       expect: c.server.digest || "" })
      : papi("/steps/" + encodeURIComponent(c.id), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: c.mine.title, body: c.mine.body, paths: textToPaths(c.mine.paths),
            inputs: textToPaths(c.mine.inputs), code: textToPaths(c.mine.code),
            expect: c.server.digest || "",
          }),
        });
    go.then(function () {
      dropDraft(c.id, c.lang);
      conflictCtx = null;
      editing = false;
      return refresh().then(refreshProjects).then(function () { toast(i18n.t("toast.conflict.mine")); });
    }).catch(fail);
  }

  /* -------------------------------------------------------------- 编辑器 */

  /* 工具栏。title 走 i18n（editor.tool.*），插入的占位文字走 editor.ph.*——
     选中一段文字再点「粗体」不会用到占位符，占位符只在没选中时出现，
     它是要落进正文的字，所以跟界面语言走是对的（用户此刻正在用这种语言操作）。 */
  var TOOLS = [
    { k: "bold", html: "<b>B</b>", wrap: ["**", "**"], ph: "bold" },
    { k: "em", html: "<i>I</i>", wrap: ["*", "*"], ph: "em" },
    { k: "code", html: "&lt;/&gt;", wrap: ["`", "`"], ph: "code" },
    { k: "h", html: "H", prefix: "## " },
    { k: "ul", html: "•", prefix: "- " },
    { k: "task", html: "☑", prefix: "- [ ] " },
    { k: "quote", html: "❞", prefix: "> " },
    { k: "pre", html: "{ }", block: "```\n\n```", back: 4 },
    { k: "link", html: "🔗", wrap: ["[", "](url)"], ph: "link" },
    { k: "img", html: "🖼" },
    { k: "table", html: "⊞" },
    { k: "hr", html: "—", block: "---" },
  ];

  /* ------------------------------------------------------------ 译文的编辑
   *
   * edLang 为空 = 编辑 note.md（原文），否则 = 编辑 note.<edLang>.md。
   * 两条路径分得很开是故意的：补翻译的工具**永远碰不到原文**（这是接口约定），
   * 所以译文那一侧既不发 paths，也不发 status/date/commit——那些结构键在
   * 翻译文件里出现一次就是把双真相源请回来。
   *
   * 冲突控制两条链各管各的：note.md 的 expect 对 s.digest，译文的 expect 对
   * **译文自己**的 digest。译文的 digest 目前不在 forest 里（Step.tr 只有
   * title/body），所以这里缓存 write 接口回给我们的那一个，并在进编辑器时
   * 尽力从 GET 端点补一次。拿不到就不带 expect——那退化成「谁最后按保存谁赢」，
   * 但只发生在译文这一份文件上，绝不会影响 note.md 那条链。
   */
  var edLang = "";
  var trDigest = Object.create(null);
  function trKey(id, l) { return id + "|" + l; }
  function noteFileName(l) { return l ? "note." + l + ".md" : "note.md"; }
  function trPath(id, l) {
    return "/steps/" + encodeURIComponent(id) + "/tr/" + encodeURIComponent(l);
  }

  function putTranslation(id, l, payload) {
    return papi(trPath(id, l), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (r && r.digest) trDigest[trKey(id, l)] = r.digest;
      return r;
    });
  }

  /* 进编辑器时补一次译文的 digest。端点不在（老服务端 / 还没接上）时安静地
     算了——少一个 expect 只是少一道闸门，不该把编辑器整个挡住。 */
  function fetchTrDigest(id, l) {
    if (MODE !== "server" || !l) return Promise.resolve("");
    return papi(trPath(id, l)).then(function (r) {
      if (r && r.digest) trDigest[trKey(id, l)] = r.digest;
      return (r && r.digest) || "";
    }, function () { return ""; });
  }

  /* 这一步能编辑哪几个语言版本：原文 + 已有的每一份译文 + 当前界面语言。
   *
   * 「当前界面语言」这一档要挡掉一种情况：一份中文笔记没写 lang:，界面又是中文，
   * 于是我们给它开一个 note.zh.md 的入口——那是把同一份中文抄成两份，正是双真相源。
   * 所以这里除了看声明的 lang:，还看正文用的是哪一套小节名（查 SECTION_NAMES
   * 那张封闭词表）。
   *
   * 注意这和 trNotice 的判据**故意不一样**：对读者说「这是中文原文」必须有
   * note.md 亲口声明的 lang:，小节名不能当证据（那是我们替他说话）；而这里只是
   * 「要不要多给一个按钮」，多给了是骚扰、少给了还能手写文件，代价小得多。
   */
  function originLang(s) {
    return s.lang || U.langByHeadings((s && s.body) || "", templates());
  }
  function editLangs(s) {
    var out = [""], seen = { "": 1 };
    Object.keys(s.tr || {}).sort().forEach(function (l) { if (!seen[l]) { seen[l] = 1; out.push(l); } });
    var cur = uiLang();
    if (!seen[cur] && cur !== originLang(s)) out.push(cur);
    return out;
  }

  function langTabs(s) {
    var langs = editLangs(s);
    if (langs.length < 2) return "";
    return '<div class="seg edlang" id="edlang">' + langs.map(function (l) {
      var label = l ? langName(l) : i18n.t("tr.badge.original");
      return '<button type="button" data-edlang="' + esc(l) + '"'
        + (l === edLang ? ' class="on"' : "") + ' title="' + esc(noteFileName(l)) + '">'
        + esc(label) + "</button>";
    }).join("") + "</div>";
  }

  /* 上次没写完的那份。不自动套上去——磁盘上那份可能才是新的。 */
  function draftBanner(s) {
    var d = readDraft(s.id, edLang);
    if (!d || sameAsStep(s, d)) return "";
    var when = d.at ? new Date(d.at).toLocaleString(i18n.locale()) : "";
    var now = edLang ? (trDigest[trKey(s.id, edLang)] || "") : (s.digest || "");
    var moved = d.base && now && d.base !== now;
    return '<div class="draftbar">'
      + "<b>" + esc(i18n.t("editor.draft.found")) + "</b>"
      + (when ? '<span class="mono">' + esc(when) + "</span>" : "")
      + (moved ? '<span class="dwarn">' + esc(i18n.t("editor.draft.moved")) + "</span>" : "")
      + '<span class="sp"></span>'
      + '<button data-draft="restore">' + esc(i18n.t("editor.draft.restore")) + "</button>"
      + '<button data-draft="discard">' + esc(i18n.t("editor.draft.discard")) + "</button>"
      + "</div>";
  }

  function renderEditor(s) {
    var base = editTarget(s, edLang);
    $("#detail").innerHTML =
      '<div class="edhead">' + crumbs(s) + langTabs(s) + '<span class="sp"></span>'
      + '<button data-act="save" class="primary">' + esc(i18n.t("editor.save")) + " <kbd>Ctrl↵</kbd></button>"
      + '<button data-act="cancel">' + esc(i18n.t("editor.cancel")) + " <kbd>Esc</kbd></button></div>"
      + draftBanner(s)
      + '<input class="title-input" id="ed-title" value="' + esc(base.title) + '" maxlength="200" placeholder="'
      + esc(i18n.t("editor.title.placeholder")) + '">'
      // 译文里没有这三块：它们是结构信息，只写在 note.md 里，写两份就是双真相源
      + (edLang ? ""
          : '<label class="edpaths">' + i18n.tHtml("editor.paths.label")
            + '<textarea id="ed-paths" rows="2" spellcheck="false" placeholder="'
            + esc(i18n.t("editor.paths.placeholder")) + '">' + esc(base.paths) + "</textarea>"
            // 多出来的那几段竖线是新的，人第一次看到时唯一能自学的地方就是这里
            + '<span class="edtip">' + i18n.tHtml("editor.paths.hint") + "</span></label>"
            + '<label class="edpaths">' + i18n.tHtml("editor.inputs.label")
            + '<textarea id="ed-inputs" rows="2" spellcheck="false" placeholder="'
            + esc(i18n.t("editor.inputs.placeholder")) + '">' + esc(base.inputs) + "</textarea>"
            + '<span class="edtip">' + esc(i18n.t("editor.inputs.hint")) + "</span></label>"
            + '<label class="edpaths">' + i18n.tHtml("editor.code.label")
            + '<textarea id="ed-code" rows="2" spellcheck="false" placeholder="'
            + esc(i18n.t("editor.code.placeholder")) + '">' + esc(base.code) + "</textarea>"
            + '<span class="edtip">' + i18n.tHtml("editor.code.hint") + "</span></label>")
      + '<div class="edtools">' + TOOLS.map(function (x) {
          return '<button type="button" data-md="' + x.k + '" title="'
            + esc(i18n.t("editor.tool." + x.k)) + '">' + x.html + "</button>";
        }).join("") + '<span class="sp"></span><span class="edhint mono" id="ed-status"></span></div>'
      + '<div class="edsplit">'
      + '<textarea class="editor" id="ed-body" spellcheck="false"></textarea>'
      + '<div class="prose edpreview" id="ed-preview"></div>'
      + "</div>"
      + '<p class="dropnote">' + i18n.tHtml("editor.hint") + "</p>"
      + '<input type="file" id="ed-file" multiple accept="image/*,.log,.txt,.csv,.tsv,.json,.py,.sh,.yaml,.yml,.pdf" hidden>';

    var ta = $("#ed-body");
    ta.value = base.body;
    bindEditor(ta, s);
    updatePreview(s);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    // digest 晚一步到也没关系：它只在按保存那一刻要用，到了就把草稿提示条重画一次
    if (edLang && !trDigest[trKey(s.id, edLang)]) {
      fetchTrDigest(s.id, edLang).then(function (d) {
        if (d && editing && selected() === s.id) {
          var bar = $("#detail .draftbar");
          if (bar) bar.outerHTML = draftBanner(s);
        }
      });
    }
  }

  /* 切换正在编辑哪个语言版本。切之前把当前这一份钉进它自己的草稿键里——
     两份稿子各存各的，切来切去一个字都不会丢。 */
  function switchEditLang(l) {
    var s = IDX[selected()];
    if (!s || l === edLang) return;
    clearTimeout(draftTimer);
    saveDraftNow();
    edLang = l;
    renderEditor(s);
  }

  var previewTimer = null;
  function updatePreview(s) {
    var pv = $("#ed-preview"), ta = $("#ed-body");
    if (!pv || !ta) return;
    pv.innerHTML = window.md.render(ta.value, { resolve: resolverFor(s) });
    enhanceProse(pv);
  }
  function schedulePreview(s) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () { updatePreview(s); }, 120);
  }

  function insertAt(ta, text, back) {
    var a = ta.selectionStart, b = ta.selectionEnd;
    ta.setRangeText(text, a, b, "end");
    if (back) ta.setSelectionRange(ta.selectionEnd - back, ta.selectionEnd - back);
    ta.focus();
  }
  function wrapSel(ta, before, after, placeholder) {
    var a = ta.selectionStart, b = ta.selectionEnd;
    var sel = ta.value.slice(a, b) || placeholder || "";
    ta.setRangeText(before + sel + after, a, b, "end");
    ta.setSelectionRange(a + before.length, a + before.length + sel.length);
    ta.focus();
  }
  function prefixLines(ta, prefix) {
    var v = ta.value, a = ta.selectionStart, b = ta.selectionEnd;
    var ls = v.lastIndexOf("\n", a - 1) + 1;
    var le = v.indexOf("\n", b);
    if (le < 0) le = v.length;
    var block = v.slice(ls, le).split("\n").map(function (l) { return prefix + l; }).join("\n");
    ta.setRangeText(block, ls, le, "end");
    ta.focus();
  }
  function insertBlock(ta, text, back) {
    var v = ta.value, a = ta.selectionStart;
    var pre = a > 0 && v[a - 1] !== "\n" ? "\n\n" : (a > 1 && v[a - 2] !== "\n" ? "\n" : "");
    var post = v[ta.selectionEnd] && v[ta.selectionEnd] !== "\n" ? "\n\n" : "\n";
    insertAt(ta, pre + text + post, (back || 0) + post.length);
  }

  /* 从 Excel / Google Sheets / 网页表格复制来的是制表符分隔的文本。
     直接转成 markdown 表格——这是科研笔记里最常见的一种粘贴。 */
  function tsvToTable(text) {
    var lines = String(text).replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
    if (lines.length < 2 || lines.some(function (l) { return l.indexOf("\t") < 0; })) return null;
    var rows = lines.map(function (l) { return l.split("\t"); });
    var n = Math.max.apply(null, rows.map(function (r) { return r.length; }));
    if (n < 2) return null;
    var cell = function (c) { return String(c == null ? "" : c).trim().replace(/\|/g, "\\|") || " "; };
    var pad = function (r) { while (r.length < n) r.push(""); return r; };
    var out = ["| " + pad(rows[0]).map(cell).join(" | ") + " |",
               "|" + rows[0].map(function () { return "---"; }).join("|") + "|"];
    rows.slice(1).forEach(function (r) { out.push("| " + pad(r).map(cell).join(" | ") + " |"); });
    return out.join("\n");
  }

  function uploadAuto(step, blob, filename) {
    var h = { "Content-Type": blob.type || "application/octet-stream" };
    if (token()) h["Authorization"] = "Bearer " + token();
    var name = filename || blob.name || "";
    if (name) h["X-Filename"] = encodeURIComponent(name);   // HTTP 头只能是 latin-1，中文名要先编码
    return fetch(BASE + "/api/p/" + encodeURIComponent(PROJECT) + "/steps/"
                 + encodeURIComponent(step.id) + "/files", { method: "POST", headers: h, body: blob })
      .then(function (r) {
        return r.text().then(function (t) {
          var j = {};
          try { j = JSON.parse(t); } catch (e) { j = { error: t.slice(0, 200) }; }
          if (!r.ok) throw new Error(j.error || r.status);
          return j;
        });
      });
  }

  function uploadIntoEditor(s, files) {
    var ta = $("#ed-body");
    $("#ed-status").textContent = i18n.t("editor.status.uploading");
    return files.reduce(function (chain, f) {
      return chain.then(function () {
        return uploadAuto(s, f).then(function (info) {
          if (IMG.test(info.path)) {
            insertBlock(ta, "![](" + info.path + ")", info.path.length + 3);  // 光标落在 ![|] 里，直接打图注
          } else {
            insertAt(ta, "[" + info.path + "](" + info.path + ")");
          }
          schedulePreview(s);
          scheduleDraft();
        });
      });
    }, Promise.resolve()).then(function () {
      $("#ed-status").textContent = i18n.t("editor.status.inserted", { n: files.length });
      setTimeout(function () { var e = $("#ed-status"); if (e) e.textContent = ""; }, 2500);
    }).catch(function (e) { $("#ed-status").textContent = ""; fail(e); });
  }

  function bindEditor(ta, s) {
    ta.addEventListener("input", function () { schedulePreview(s); scheduleDraft(); });
    // 标题、外部路径、数据依赖、代码位置同样是人敲进去的，一起进草稿
    ["#ed-title", "#ed-paths", "#ed-inputs", "#ed-code"].forEach(function (sel) {
      var el = $(sel);
      if (el) el.addEventListener("input", scheduleDraft);
    });

    ta.addEventListener("paste", function (e) {
      var dt = e.clipboardData;
      if (!dt) return;
      var files = [];
      for (var i = 0; i < (dt.items || []).length; i++) {
        if (dt.items[i].kind === "file") {
          var f = dt.items[i].getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) { e.preventDefault(); uploadIntoEditor(s, files); return; }
      var tbl = tsvToTable(dt.getData("text/plain") || "");
      if (tbl) {
        e.preventDefault();
        insertBlock(ta, tbl);
        schedulePreview(s);
        toast(i18n.t("toast.table.converted"));
      }
    });

    ["dragenter", "dragover"].forEach(function (ev) {
      ta.addEventListener(ev, function (e) { e.preventDefault(); ta.classList.add("drop"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      ta.addEventListener(ev, function () { ta.classList.remove("drop"); });
    });
    ta.addEventListener("drop", function (e) {
      var files = Array.prototype.slice.call(e.dataTransfer.files || []);
      if (!files.length) return;
      e.preventDefault();
      uploadIntoEditor(s, files);
    });

    $("#ed-file").addEventListener("change", function (e) {
      var files = Array.prototype.slice.call(e.target.files || []);
      if (files.length) uploadIntoEditor(s, files);
      e.target.value = "";
    });

    $("#detail").querySelectorAll("[data-md]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.preventDefault();
        var tool = TOOLS.filter(function (x) { return x.k === b.getAttribute("data-md"); })[0];
        if (!tool) return;
        if (tool.k === "img") { $("#ed-file").click(); return; }
        if (tool.k === "table") {
          // 表头那三个字是要落进正文的内容，跟的是这一份记录的语言，不是界面语言
          insertBlock(ta, i18n.tIn(edLang || s.lang || uiLang(), "template.table"));
        } else if (tool.wrap) {
          wrapSel(ta, tool.wrap[0], tool.wrap[1], i18n.t("editor.ph." + tool.ph));
        } else if (tool.prefix) {
          prefixLines(ta, tool.prefix);
        } else if (tool.block) {
          insertBlock(ta, tool.block, tool.back);
        }
        schedulePreview(s);
        scheduleDraft();   // setRangeText 不触发 input 事件，工具栏插入的内容得自己钉
      });
    });
  }

  function saveEditor() {
    var s = IDX[selected()];
    var st = editorState();
    if (!s || !st) return;
    clearTimeout(draftTimer);
    saveDraftNow();                       // 先钉住：网络失败、409、页面被关都不该让这段字消失
    /* 两条写入路径。译文那条只发 title 和 body：补翻译的工具碰不到原文，
       所以它连 paths / status 都不该有能力发出去。 */
    var go = st.lang
      ? putTranslation(s.id, st.lang, {
          title: st.title, body: st.body,
          expect: trDigest[trKey(s.id, st.lang)] || "",
        }).then(refresh)
      : patch(s.id, {
          title: st.title,
          body: st.body,
          paths: textToPaths(st.paths),
          // 三块结构化文本一起回写。整组替换是对的：框里显示的就是磁盘上的
          // 全部内容（`commit:` 派生出来的那条除外，见 codeToText），
          // 删掉一行的意思就该是删掉那一行。
          inputs: textToPaths(st.inputs),
          code: textToPaths(st.code),
          // 乐观并发控制：expect 是我打开这一步时读到的摘要。这期间别人改过就 409，
          // 由人来判怎么合，而不是谁最后按保存谁赢。
          expect: s.digest || "",
        });
    return go
      .then(function () {
        dropDraft(s.id, st.lang);
        editing = false; renderDetail(); refreshProjects(); toast(i18n.t("toast.saved"));
      })
      .catch(function (e) {
        if (e && e.status === 409) return handleConflict(s, st, e);
        fail(e);
      });
  }

  /* -------------------------------------------------------------- ① 移动 */

  /* P2 的地基是「不丢历史」，不是「不能改结构」——记下来就不丢。于是 parent 可以
   * 改，但每一次改都要留下一条 `moved:` 审计，而**原因是唯一无法自动生成的部分**。
   *
   * 所以这个框有两条硬规矩：
   *   1) 原因输入框不是可选的。一个可选的框会让人先点了确定才发现要写，
   *      转头就回去用「把两步的正文对调」那种不留痕迹的老办法。
   *   2) 成环 / 挂到自己的后代下面**当场**说，不等服务端 4xx——那两条不是笔误，
   *      是想法本身有问题，等一个 400 回来时人已经点过确定了。
   */
  function openMove(sid) {
    var s = IDX[sid];
    if (!s) return;
    $("#mv-title").textContent = i18n.t("move.dialog.title", { id: s.id });
    var cur = s.parent || "";
    var opts = ['<option value="">' + esc(i18n.t("move.parent.none")) + "</option>"];
    F.steps.forEach(function (o) {
      if (o.id === s.id) return;
      var bad = U.moveError(IDX, s.id, o.id);
      opts.push('<option value="' + esc(o.id) + '"' + (o.id === cur ? " selected" : "") + ">"
        + esc(o.id + "  " + (stepTitle(o) || i18n.t("common.untitled")).slice(0, 40)
              + (o.id === cur ? "  · " + i18n.t("move.parent.current", { id: cur }) : "")
              + (bad === "descendant" ? "  ⚠" : ""))
        + "</option>");
    });
    $("#mv-parent").innerHTML = opts.join("");
    $("#mv-parent").value = cur;
    $("#mv-reason").value = "";
    $("#dlg-move").dataset.sid = s.id;
    paintMoveErr();
    $("#dlg-move").showModal();
    setTimeout(function () { $("#mv-reason").focus(); }, 30);
  }

  /* 选中的目标当场判一次。返回的是「能不能提交」，顺带把话说在按钮旁边。 */
  function paintMoveErr() {
    var sid = $("#dlg-move").dataset.sid, box = $("#mv-err");
    var parent = $("#mv-parent").value;
    var code = U.moveError(IDX, sid, parent);
    var msg = "";
    if (code === "self") msg = i18n.t("move.err.self", { id: sid });
    else if (code === "descendant") msg = i18n.t("move.err.descendant", { id: sid, parent: parent });
    else if (code === "noop") msg = i18n.t("move.err.noop", { id: sid, parent: parent || "" });
    else if (code === "missing") msg = i18n.t("move.err.missing", { parent: parent });
    box.textContent = msg;
    box.hidden = !msg;
    $("#mv-ok").disabled = !!msg;
    return !msg;
  }

  function submitMove() {
    var sid = $("#dlg-move").dataset.sid, s = IDX[sid];
    if (!s || !paintMoveErr()) return;
    var parent = $("#mv-parent").value;
    var reason = $("#mv-reason").value.trim();
    if (!reason) { toast(i18n.t("move.err.reason"), true); $("#mv-reason").focus(); return; }
    $("#dlg-move").close();
    /* 走的是同一条 PATCH：payload 里带 parent + reason，服务端把它转给
       W.move_step（它才是唯一写审计的地方）。缺原因时服务端也会拒，这里那道
       校验是为了让人在**还看着这个框**的时候就知道。 */
    papi("/steps/" + encodeURIComponent(sid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent: parent || null, reason: reason, author: "human",
                             date: todayISO(), expect: s.digest || "" }),
    }).then(function () {
      return refresh().then(function () {
        toast(parent ? i18n.t("toast.moved", { id: sid, parent: parent })
                     : i18n.t("toast.moved.root", { id: sid }));
      });
    }).catch(fail);
  }

  /* -------------------------------------------------------------- 写入 */

  function patch(id, body) {
    return papi("/steps/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function () { return refresh(); });
  }

  /* 新建对话框里的正文同样要存草稿：<dialog> 按 Esc 直接就关了，而「为什么」这一段
     常常是先写十分钟才想好标题的。草稿按项目存一份，不区分父节点。 */
  function newDraft() { return readDraft(NEW_DRAFT_ID); }
  function isPristineTemplate(text) {
    return text === templateBody("zh") || text === templateBody("en");
  }
  function saveNewDraft() {
    var b = $("#nf-body");
    if (!b || !$("#dlg-new").open) return;
    var d = { title: $("#nf-title").value, body: b.value, paths: $("#nf-paths").value,
              inputs: $("#nf-inputs").value, code: $("#nf-code").value,
              commit: $("#nf-commit").value, status: $("#nf-status").value,
              lang: $("#nf-lang").value,
              parent: $("#nf-parent").dataset.pid || "", at: Date.now() };
    // 只有模板原样没动、其余都空时才算「没写东西」。判定要认两种语言的模板，
    // 否则切一下内容语言就会留下一份「什么都没写」的草稿在下次弹出来。
    if (!d.title.trim() && isPristineTemplate(d.body) && !d.paths.trim()) { dropDraft(NEW_DRAFT_ID); return; }
    writeDraft(NEW_DRAFT_ID, d);
  }

  function openNew(parentId) {
    var p = IDX[parentId];
    $("#nf-parent").value = p ? p.id + "  " + stepTitle(p) : i18n.t("newstep.parent.none");
    $("#nf-parent").dataset.pid = p ? p.id : "";
    $("#nf-title").value = "";
    // 内容语言：默认值是从兄弟步骤已经在用的小节名推出来的，用户可以当场改。
    // 改的是**正文模板**，不是界面。词表之外的语言（ja…）没有模板可插，
    // 退到 DEFAULT——那几行小节名删掉就是了，比插一套谁也认不出的标题好。
    var guess = guessContentLang(p);
    $("#nf-lang").value = i18n.STRINGS[guess] ? guess : i18n.DEFAULT;
    $("#nf-body").value = templateBody($("#nf-lang").value);
    $("#nf-date").value = todayISO();
    $("#nf-status").value = "wip";
    $("#nf-commit").value = "";
    // 从父步骤继承路径和代码位置：同一条线上多半没变，改比重打省事。
    // 但**核对结果和度量不跟着走**（U.inheritPath 抹掉 checked/missing/md5/size/n）：
    // 那些是「有人真去看过一眼」的结论，抄给一个还没跑过的步骤就是伪造证据。
    // **数据依赖不继承**：它说的是「这一次消费了哪份产物」，照抄一遍就是替人
    // 编造一条他没做过的声明。空着的意思正是「数据就是从 parent 下来的」。
    $("#nf-paths").value = p ? (p.paths || []).map(U.inheritPath).map(U.formatPath).join("\n") : "";
    $("#nf-code").value = p ? codeToText(p) : "";
    $("#nf-inputs").value = "";

    var d = newDraft();
    $("#nf-draft").hidden = !d;
    if (d) {
      $("#nf-draft-when").textContent = d.at ? new Date(d.at).toLocaleString(i18n.locale()) : "";
      $("#nf-draft-title").textContent = d.title || i18n.t("newstep.draft.untitled");
    }
    $("#dlg-new").showModal();
    setTimeout(function () { $("#nf-title").focus(); }, 30);
  }

  function restoreNewDraft() {
    var d = newDraft();
    if (!d) return;
    $("#nf-title").value = d.title || "";
    $("#nf-body").value = d.body || templateBody($("#nf-lang").value);
    $("#nf-paths").value = d.paths || "";
    $("#nf-inputs").value = d.inputs || "";
    $("#nf-code").value = d.code || "";
    $("#nf-commit").value = d.commit || "";
    if (d.status) $("#nf-status").value = d.status;
    if (d.lang && i18n.STRINGS[d.lang]) $("#nf-lang").value = d.lang;
    if (d.parent && IDX[d.parent]) {
      $("#nf-parent").dataset.pid = d.parent;
      $("#nf-parent").value = d.parent + "  " + stepTitle(IDX[d.parent]);
    }
    $("#nf-draft").hidden = true;
    toast(i18n.t("toast.draft.restored"));
  }

  function submitNew() {
    var title = $("#nf-title").value.trim();
    if (!title) { toast(i18n.t("toast.title.required"), true); return; }
    var wantInputs = textToPaths($("#nf-inputs").value);
    var wantCode = textToPaths($("#nf-code").value);
    papi("/steps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parent: $("#nf-parent").dataset.pid || null,
        title: title,
        status: $("#nf-status").value,
        date: $("#nf-date").value,
        commit: $("#nf-commit").value.trim(),
        author: "human",
        body: $("#nf-body").value,
        paths: textToPaths($("#nf-paths").value),
        inputs: wantInputs,
        code: wantCode,
        // 把下拉框里选的内容语言**写进 note.md**。不写的话这次选择只影响插入的
        // 模板，转头就丢了：读的一侧再打开这一步，只能从小节名倒推，而对没翻译
        // 的记录界面就只能说「这是原文」——说不出是哪种语言的原文。
        // 这是一个用户看得见、能当场改的下拉框，所以它是**声明**，不是猜。
        lang: $("#nf-lang").value,
      }),
    }).then(function (step) {
      dropDraft(NEW_DRAFT_ID);
      /* 建步骤那条路由**当前**还没有把 inputs / code 透传给写入层（见报告里的
         接缝清单）。少写两个字段是静默丢数据，所以这里补一次 PATCH——那条路
         已经收这两个键了。服务端补上之后这次 PATCH 自然不会发生（返回值里
         已经有了），不需要回来改这段。 */
      var lack = (wantInputs.length && !(step.inputs || []).length)
        || (wantCode.length && !(step.code || []).filter(function (c) { return c.from !== "commit"; }).length);
      var go = lack
        ? papi("/steps/" + encodeURIComponent(step.id), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ inputs: wantInputs, code: wantCode }),
          }).catch(fail)
        : Promise.resolve();
      return go.then(refresh).then(function () {
        forceSelect(step.id);
        scrollToSelected();
        refreshProjects();
        toast(i18n.t("toast.created", { id: step.id }));
      });
    }).catch(fail);
  }

  /* -------------------------------------------------------------- 事件 */

  function onHash() { renderSelection(); renderDetail(); }

  /* 点「编辑正文」时先落在**你此刻正在读的那一份**上：屏幕上显示着英文译文、
     一按编辑却打开了中文原文，是最容易让人改错文件的一种设计。 */
  function startEditing() {
    var s = IDX[selected()];
    var pick = s ? U.pickLang(s, uiLang()) : null;
    edLang = (pick && pick.tr) ? uiLang() : "";
    editing = true;
    renderDetail();
  }

  document.addEventListener("click", function (e) {
    var zb = e.target.closest("#zoombar button");
    if (zb) {
      var k = zb.getAttribute("data-zoom");
      if (k === "in") setZoom(zoom * 1.25);
      else if (k === "out") setZoom(zoom / 1.25);
      else if (k === "reset") setZoom(1);
      else fitZoom();
      return;
    }

    var cp = e.target.closest("[data-copy]");
    if (cp) {
      e.preventDefault();
      navigator.clipboard.writeText(cp.getAttribute("data-copy"))
        .then(function () { toast(i18n.t("toast.copied.path")); },
              function () { toast(i18n.t("toast.copy.failed"), true); });
      return;
    }

    var zi = e.target.closest("img.zoomable");
    if (zi) { e.preventDefault(); openLightbox(zi); return; }
    if (e.target.closest("#lightbox")) { closeLightbox(); return; }

    // 三个对话框的按钮：离开确认 / 冲突 / 草稿条。都在最前面处理——
    // 它们出现的时刻正是「再点错一下就丢东西」的时刻。
    var lv = e.target.closest("[data-leave]");
    if (lv) { e.preventDefault(); resolveLeave(lv.getAttribute("data-leave")); return; }
    var cf = e.target.closest("[data-conflict]");
    if (cf) { e.preventDefault(); resolveConflict(cf.getAttribute("data-conflict")); return; }
    var dr = e.target.closest("[data-draft]");
    if (dr) {
      e.preventDefault();
      var ds = IDX[selected()], how = dr.getAttribute("data-draft"), dd = ds && readDraft(ds.id, edLang);
      if (!ds) return;
      if (how === "restore" && dd) {
        $("#ed-title").value = dd.title || "";
        $("#ed-body").value = dd.body || "";
        if ($("#ed-paths")) $("#ed-paths").value = dd.paths || "";
        if ($("#ed-inputs")) $("#ed-inputs").value = dd.inputs || "";
        if ($("#ed-code")) $("#ed-code").value = dd.code || "";
        updatePreview(ds);
        toast(i18n.t("toast.draft.restored"));
      } else {
        dropDraft(ds.id, edLang);
        toast(i18n.t("toast.draft.discarded"));
      }
      // 只找详情面板里那一条：新建对话框的 #nf-draft 用的是同一个类
      var bar = $("#detail .draftbar");
      if (bar) bar.remove();
      return;
    }
    if (e.target.closest("[data-newdraft]")) {
      e.preventDefault();
      if (e.target.closest("[data-newdraft]").getAttribute("data-newdraft") === "restore") restoreNewDraft();
      else { dropDraft(NEW_DRAFT_ID); $("#nf-draft").hidden = true; toast(i18n.t("toast.draft.discarded")); }
      return;
    }
    var el = e.target.closest("[data-edlang]");
    if (el) { e.preventDefault(); switchEditLang(el.getAttribute("data-edlang")); return; }
    var lg = e.target.closest("[data-lang]");
    if (lg) { e.preventDefault(); i18n.setLang(lg.getAttribute("data-lang")); return; }
    if (e.target.closest("[data-newproj]")) { e.preventDefault(); newProject(); return; }
    if (e.target.closest("[data-gitretry]")) { e.preventDefault(); retrySync(); return; }
    var gh = e.target.closest("#hitlist a");
    if (gh) { hideHits(); return; }   // 让链接照常跳走，只把面板收起来

    var goto = e.target.closest("[data-goto]");
    if (goto) { e.preventDefault(); select(goto.getAttribute("data-goto")); scrollToSelected(); return; }

    var hit = e.target.closest("#rows .row, #dnodes .card, #fnodes .fcard");
    if (hit) { select(hit.getAttribute("data-id")); return; }

    // 点图上的空白处 = 取消选中 = 所有节点回到全亮。
    // 不透明度这个通道只承载"是否在选中的祖先链上"，没有选中时就不该有任何东西是淡的。
    if (e.target.closest("#scroller") && selected()) { select(""); return; }

    var vt = e.target.closest("#viewtoggle button");
    if (vt) {
      view = vt.getAttribute("data-view");
      localStorage.setItem("trace.view", view);
      applyView(); renderSelection(); scrollToSelected();
      return;
    }

    var rm = e.target.closest("[data-rm]");
    if (rm) {
      e.preventDefault();
      var s0 = IDX[selected()], path = rm.getAttribute("data-rm");
      if (!s0 || !confirm(i18n.t("confirm.file.delete", { path: path }))) return;
      papi("/steps/" + encodeURIComponent(s0.id) + "/files/" + path.split("/").map(encodeURIComponent).join("/"),
           { method: "DELETE" }).then(refresh).catch(fail);
      return;
    }

    var st = e.target.closest("[data-status]");
    if (st) { patch(selected(), { status: st.getAttribute("data-status") }).then(refreshProjects).catch(fail); return; }

    var ai = e.target.closest("[data-add-insight]");
    if (ai) {
      var kind = ai.getAttribute("data-add-insight");
      var label = insightLabel(kind);
      var txt = prompt(i18n.t("insight.prompt", { label: label }));
      if (!txt || !txt.trim()) return;
      var sid = selected();
      patchProject({ add_insight: { kind: kind, text: txt.trim() + (sid ? " —— [[" + sid + "]]" : "") } })
        .then(function () { toast(i18n.t("toast.insight.added", { label: label })); }).catch(fail);
      return;
    }

    /* ④ 折叠开关。被取代的那几条默认收起——它们不是当前结论；但它们必须
       一直在页面上够得着，因为「我当初信的是什么」正是这段记录的价值所在。 */
    var fold = e.target.closest("[data-ins-fold]");
    if (fold) {
      e.preventDefault();
      // 变量名不叫 kind：同一个事件处理函数里「＋ 洞察」那一支已经有一个 kind，
      // var 是函数作用域的，撞名之后出错的地方离现场很远。
      var foldKind = fold.getAttribute("data-ins-fold");
      var box = document.querySelector('[data-old="' + foldKind + '"]');
      if (!box) return;
      box.hidden = !box.hidden;
      fold.textContent = box.hidden
        ? i18n.t("insight.superseded.show", { n: box.children.length })
        : i18n.t("insight.superseded.hide");
      return;
    }

    /* 「改这一条」和「取代这一条」是两件不同的事，i18n 的 insight.supersede.hint
       写清了怎么选：说错了话就改，想错了事就取代。改是就地重写那一行（id 不变，
       指向它的引用继续有效）；取代是新写一条，旧的一个字都不动。 */
    var ied = e.target.closest("[data-ins-edit]");
    if (ied) {
      e.preventDefault();
      var eid = ied.getAttribute("data-ins-edit");
      var got = findInsight(eid);
      if (!got) return;
      var newText = prompt(i18n.t("insight.item.edit.prompt", { id: eid }), got.item.text);
      if (!newText || !newText.trim() || newText.trim() === got.item.text) return;
      patchProject({ add_insight: { kind: got.kind, text: newText.trim(), id: eid } })
        .then(function () { toast(i18n.t("toast.insight.updated", { id: eid })); }).catch(fail);
      return;
    }
    var isu = e.target.closest("[data-ins-sup]");
    if (isu) {
      e.preventDefault();
      var oid = isu.getAttribute("data-ins-sup");
      var old = findInsight(oid);
      if (!old) return;
      var txt = prompt(i18n.t("insight.supersede.prompt", { id: oid }));
      if (!txt || !txt.trim()) return;
      patchProject({ add_insight: { kind: old.kind, text: txt.trim(), supersedes: oid } })
        .then(function (r) {
          // id 优先用服务端回的那一个（它才是分配者）；老服务端不回传时从刚刷新的
          // 正文里反查「谁取代了 oid」——那是派生的，本来就不该有第二个来源。
          var neu = (r && r.insight && r.insight.id) || "";
          if (!neu) {
            var items = U.parseInsights(insightBody());
            Object.keys(items).forEach(function (k) {
              items[k].forEach(function (it) { if (it.supersedes.indexOf(oid) >= 0) neu = it.id; });
            });
          }
          toast(i18n.t("toast.insight.superseded", { id: neu, old: oid }));
        }).catch(fail);
      return;
    }

    var act = e.target.closest("[data-act]");
    if (!act) return;
    var name = act.getAttribute("data-act");
    if (name === "edit-insights") { openInsightEditor(); return; }
    if (name === "save-insights") { saveInsights(); return; }
    if (name === "move") { openMove(selected()); return; }
    if (name === "edit") { startEditing(); }
    else if (name === "cancel") { guardLeave(function () { editing = false; renderDetail(); }); }
    else if (name === "child") { openNew(selected()); }
    else if (name === "delete") {
      var d = IDX[selected()];
      if (!d) return;
      var kids = (d.children || []).length;
      // 删除会打断三种边，不是一种。子步骤（parent）以前就说了；`input:` 那条边是
      // 这一版新加的，而且后果更重——可溯源性沿着它上溯，再叠上「id 会被重用」，
      // 那些边会无声地改指到别的步骤上。所以三条一起在**动手之前**摆出来。
      var eaters = (d.consumers || []).length, refs = (d.backlinks || []).length;
      var why = prompt([
        i18n.t("confirm.delete.title", { id: d.id, title: stepTitle(d) }),
        "",
        i18n.t("confirm.delete.what"),
        kids ? i18n.t("confirm.delete.children", { n: kids }) : "",
        eaters ? i18n.t("confirm.delete.consumers", { n: eaters, id: d.id }) : "",
        refs ? i18n.t("confirm.delete.refs", { n: refs, id: d.id }) : "",
        (eaters || refs) ? i18n.t("confirm.delete.reuse") : "",
        i18n.t("confirm.delete.dead"),
        "",
        i18n.t("confirm.delete.why"),
      ].filter(Boolean).join("\n"));
      if (!why || !why.trim()) return;
      papi("/steps/" + encodeURIComponent(d.id), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: why.trim(), by: "human", date: todayISO() }),
      }).then(function (info) {
        // 目录都没了，留着草稿只会在别的步骤上误弹——译文那几份草稿一起清
        dropDraft(d.id);
        Object.keys(d.tr || {}).forEach(function (l) { dropDraft(d.id, l); });
        forceSelect("");
        return refresh().then(refreshProjects).then(function () {
          // 指空的 input 排在孤儿前面：孤儿会被降级为根、在图上仍然看得见，
          // 而一条指空的 input 在界面上只是一行小字，最容易就这么留在那里。
          toast(info.dangling_inputs && info.dangling_inputs.length
            ? i18n.t("toast.deleted.inputs", { id: info.id, ids: info.dangling_inputs.join(" · ") })
            : (info.orphaned.length
              ? i18n.t("toast.deleted.orphaned", { id: info.id, ids: info.orphaned.join(" · ") })
              : i18n.t("toast.deleted", { id: info.id })));
        });
      }).catch(fail);
    }
    else if (name === "save") { saveEditor(); }
  });

  $("#proj").addEventListener("change", function (e) {
    location.href = e.target.value ? projectHref(e.target.value) : homeHref();
  });
  $("#search").addEventListener("input", function (e) {
    query = e.target.value;
    renderSelection();
    if (!PROJECT) renderHome();     // 项目索引页：搜索词也用来筛项目卡片
    scheduleGlobalSearch();
  });
  $("#search").addEventListener("focus", function () { if (hitsShown()) showHits(); });
  $("#btn-scope").addEventListener("click", function () {
    scopeAll = !scopeAll;
    localStorage.setItem("trace.scope", scopeAll ? "all" : "one");
    paintScope();
    scheduleGlobalSearch();
  });
  $("#btn-new").addEventListener("click", function () { openNew(selected()); });
  $("#btn-token").addEventListener("click", function () {
    var got = prompt(i18n.t("confirm.token"), token());
    if (got !== null) {
      setToken(got.trim());
      toast(i18n.t(got.trim() ? "toast.token.saved" : "toast.token.cleared"));
    }
  });
  /* 内容语言只改模板，而且**只在正文还是没动过的模板时**改：人已经开始写了，
     换语言绝不能把那几行冲掉。 */
  $("#nf-lang").addEventListener("change", function () {
    var b = $("#nf-body");
    if (isPristineTemplate(b.value)) b.value = templateBody($("#nf-lang").value);
    saveNewDraft();
  });
  $("#nf-ok").addEventListener("click", function (e) { e.preventDefault(); $("#dlg-new").close(); submitNew(); });
  /* 移动对话框。选到一个不能挂的目标时**当场**说，而不是等服务端的 4xx：
     那时人已经点过确定，注意力也已经从「我到底想把它挂到哪」上移开了。 */
  $("#mv-parent").addEventListener("change", paintMoveErr);
  $("#mv-ok").addEventListener("click", function (e) { e.preventDefault(); submitMove(); });
  ["#nf-title", "#nf-body", "#nf-paths", "#nf-inputs", "#nf-commit", "#nf-code"].forEach(function (sel) {
    var el = $(sel);
    if (el) el.addEventListener("input", saveNewDraft);
  });
  // <dialog> 按 Esc 会直接关，关之前把没写完的东西钉住
  $("#dlg-new").addEventListener("close", saveNewDraft);

  /* 浏览器后退键会绕过 select() 的确认框直接换 hash。这里不弹窗（用户按的是后退，
     拦住它更烦人），改成把草稿钉死再走——内容一个字都不会丢，下次进这一步会被问。 */
  window.addEventListener("hashchange", function () {
    if (editing && isDirty()) {
      clearTimeout(draftTimer);
      saveDraftNow();
      toast(i18n.t("toast.draft.kept"));
    }
    editing = false;
    edLang = "";
    onHash();
  });

  /* 关标签页 / 刷新。草稿已经落盘了，这道确认防的是「以为自己刚才按过保存」。 */
  window.addEventListener("beforeunload", function (e) {
    if (!isDirty()) return;
    clearTimeout(draftTimer);
    saveDraftNow();
    e.preventDefault();
    e.returnValue = "";
  });

  // Ctrl/⌘ + 滚轮缩放图视图
  $("#scroller").addEventListener("wheel", function (e) {
    if (!(e.ctrlKey || e.metaKey) || view !== "graph") return;
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });

  document.addEventListener("keydown", function (e) {
    if (!$("#lightbox").hidden && e.key === "Escape") { closeLightbox(); return; }
    if ($("#dlg-leave").open || $("#dlg-conflict").open) {
      // 这两个框问的正是「要不要丢东西」，Esc 一律理解成「先别动，我再想想」
      if (e.key === "Escape") {
        e.preventDefault();
        if ($("#dlg-leave").open) resolveLeave("stay"); else resolveConflict("cancel");
      }
      return;
    }
    if (hitsShown() && e.key === "Escape") { e.preventDefault(); hideHits(); return; }
    var t = e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") {
      if (e.target.id === "ed-insights") {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveInsights(); }
        if (e.key === "Escape") { e.preventDefault(); onHash(); }
        return;
      }
      if (e.target.id === "ed-body" || e.target.id === "ed-title" || e.target.id === "ed-paths"
          || e.target.id === "ed-inputs" || e.target.id === "ed-code") {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveEditor(); return; }
        if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) {
          e.preventDefault(); wrapSel($("#ed-body"), "**", "**", i18n.t("editor.ph.bold"));
          schedulePreview(IDX[selected()]); scheduleDraft(); return;
        }
        if ((e.metaKey || e.ctrlKey) && (e.key === "i" || e.key === "I")) {
          e.preventDefault(); wrapSel($("#ed-body"), "*", "*", i18n.t("editor.ph.em"));
          schedulePreview(IDX[selected()]); scheduleDraft(); return;
        }
        // Esc 从前是直接丢弃。写了十分钟的正文按一下退出全屏/退出输入法就没了，
        // 而且没有任何撤销入口——现在先存草稿再问。
        if (e.key === "Escape") {
          e.preventDefault();
          guardLeave(function () { editing = false; renderDetail(); });
          return;
        }
      }
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (e.key === "/") { e.preventDefault(); $("#search").focus(); return; }
    if (!F.steps.length) return;
    var i = F.steps.findIndex(function (s) { return s.id === selected(); });
    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault(); select(F.steps[Math.min(F.steps.length - 1, i + 1)].id); scrollToSelected();
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault(); select(F.steps[Math.max(0, i <= 0 ? 0 : i - 1)].id); scrollToSelected();
    } else if (e.key === "g") {
      // 三个视图轮着来。数据流也进这个循环——它不是一个附属面板，而是同一份
      // 文件的第三种读法，和图/列表平级。
      view = VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length];
      localStorage.setItem("trace.view", view);
      applyView(); renderSelection(); scrollToSelected();
    } else if (e.key === "n" && canWrite()) { e.preventDefault(); openNew(selected()); }
    else if (e.key === "e" && canWrite() && selected()) { e.preventDefault(); startEditing(); }
    else if (e.key === "Escape") {
      if (editing) guardLeave(function () { editing = false; renderDetail(); });
      else select("");
    }
  });

  // 阅读模式下拖文件到详情面板：上传并追加到正文末尾
  if (canWrite() && PROJECT) {
    var det = $("#detail");
    ["dragenter", "dragover"].forEach(function (ev) {
      det.addEventListener(ev, function (e) { if (!editing) { e.preventDefault(); det.classList.add("drop"); } });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      det.addEventListener(ev, function () { det.classList.remove("drop"); });
    });
    det.addEventListener("drop", function (e) {
      if (editing) return;
      e.preventDefault();
      var s = IDX[selected()];
      if (!s) { toast(i18n.t("toast.select.step"), true); return; }
      var files = Array.prototype.slice.call(e.dataTransfer.files || []);
      if (!files.length) return;
      // 追加进的永远是 note.md 的正文：附件属于这一步，不属于某一种语言，
      // 而这里没有编辑器摆两份给人选。译文要引用同一张图，在译文里自己写一行即可。
      var body = s.body || "";
      files.reduce(function (chain, f) {
        return chain.then(function () {
          return uploadAuto(s, f).then(function (info) {
            body = body.replace(/\s+$/, "") + "\n\n"
              + (IMG.test(info.path) ? "!" : "") + "[" + info.path + "](" + info.path + ")\n";
          });
        });
      }, Promise.resolve())
        .then(function () { return patch(s.id, { body: body }); })
        .then(function () { toast(i18n.t("toast.uploaded", { n: files.length })); })
        .catch(fail);
    });
  }

  /* -------------------------------------------------------------- 跨项目搜索 */

  /* FORMAT.md 第 0 节写死「人和 LLM 信息对等」。agent 一句 trace_search 不给 project
     就搜遍所有项目，人这边原来只能一个课题一个课题点进去各搜一遍——而「我当年好像
     在某个项目里试过对比学习，后来放弃了」恰恰是记不清在哪个项目里的那类问题。
     所以搜索框加一个范围开关，项目索引页默认就是全局。

     取数据分两条路：优先打服务端的搜索端点（一次请求搜全部）；端点不在就退回逐个
     项目拉 forest 在浏览器里搜。退路不是可有可无——静态导出和老版本服务端都没有
     那个端点，而「搜不到」这件事人是察觉不到的，只会以为自己记错了。 */
  var scopeAll = localStorage.getItem("trace.scope") === "all";
  var gsearchTimer = null, gsearchSeq = 0;
  var forestCache = Object.create(null);

  function paintScope() {
    var b = $("#btn-scope");
    b.textContent = i18n.t(scopeAll ? "app.scope.all" : "app.scope.one");
    b.classList.toggle("on", scopeAll);
    b.title = i18n.t(scopeAll ? "app.scope.all.title" : "app.scope.one.title");
    b.hidden = MODE !== "server";
  }
  function hitsShown() { return !$("#hitlist").hidden; }
  function showHits() { $("#hitlist").hidden = false; }
  function hideHits() { $("#hitlist").hidden = true; }

  function whereLabel(w) {
    var key = "search.where." + w;
    return i18n.has(key) ? i18n.t(key) : String(w || "");
  }

  function normalizeHits(d) {
    var arr = Array.isArray(d) ? d : (d.hits || d.results || d.steps || []);
    var rows = arr.map(function (h) {
      return {
        slug: h.project || h.slug || h.project_slug || "",
        name: h.project_name || h.name || h.project || "",
        id: h.id || h.step || "",
        title: h.title || "",
        status: h.status || "wip",
        date: h.date || "",
        body: h.snippet || h.excerpt || h.body || "",
        where: h.where || [],
      };
    }).filter(function (h) { return h.slug && h.id; });
    // total 照实报，这样能说「还有 200 条没显示」，而不是让人以为搜完了
    return { rows: rows, total: (d && typeof d.total === "number") ? d.total : rows.length };
  }

  function searchRemote(q) {
    return api("/api/search?q=" + encodeURIComponent(q) + "&limit=80").then(normalizeHits);
  }

  /* 端点不在（老服务端）时的退路：逐个项目拉 forest 在浏览器里搜。
     判定用 U.matches，和服务端 search_hits / MCP trace_search 覆盖同样的字段。 */
  function searchLocally(q) {
    var names = {};
    PROJECTS.forEach(function (p) { names[p.slug] = p.name || p.slug; });
    return PROJECTS.map(function (p) { return p.slug; }).reduce(function (chain, slug) {
      return chain.then(function (acc) {
        var got = slug === PROJECT
          ? Promise.resolve({ steps: F.steps })
          : (forestCache[slug]
              ? Promise.resolve(forestCache[slug])
              : api("/api/p/" + encodeURIComponent(slug) + "/forest").then(function (f) {
                  forestCache[slug] = f;
                  return f;
                }));
        return got.then(function (f) {
          (f.steps || []).forEach(function (s) {
            if (!U.matches(s, q)) return;
            acc.push({ slug: slug, name: names[slug] || slug, id: s.id, title: s.title,
                       status: s.status, date: s.date, body: s.body, where: [] });
          });
          return acc;
        }, function () { return acc; });
      });
    }, Promise.resolve([])).then(function (rows) {
      return { rows: rows, total: rows.length };
    });
  }

  function scheduleGlobalSearch() {
    clearTimeout(gsearchTimer);
    gsearchTimer = setTimeout(runGlobalSearch, 220);
  }

  function runGlobalSearch() {
    var q = query.trim();
    var wantGlobal = MODE === "server" && (scopeAll || !PROJECT);
    if (!wantGlobal || q.length < 2) {
      hideHits();
      if (!wantGlobal && q && MODE === "static" && !PROJECT) {
        $("#hitlist").innerHTML = '<p class="dropnote">' + i18n.tHtml("search.static") + "</p>";
        showHits();
      }
      return;
    }
    var seq = ++gsearchSeq;
    $("#hitlist").innerHTML = '<p class="dropnote">' + esc(i18n.t("search.searching")) + "</p>";
    showHits();
    searchRemote(q).catch(function () { return searchLocally(q); })
      .then(function (res) {
        if (seq !== gsearchSeq) return;    // 打字比请求快，只认最后一次
        paintHits(q, res.rows, res.total);
      })
      .catch(function (e) {
        if (seq !== gsearchSeq) return;
        $("#hitlist").innerHTML = '<p class="dropnote">'
          + i18n.tHtml("search.failed", { error: String((e && e.message) || e) }) + "</p>";
      });
  }

  function paintHits(q, hits, total) {
    if (!hits.length) {
      $("#hitlist").innerHTML = '<p class="dropnote">'
        + i18n.tHtml("search.none", { q: q, projects: i18n.t("count.projects", { n: PROJECTS.length }) })
        + "</p>";
      return;
    }
    var byProj = {}, order = [];
    hits.forEach(function (h) {
      if (!byProj[h.slug]) { byProj[h.slug] = []; order.push(h.slug); }
      byProj[h.slug].push(h);
    });
    var more = total > hits.length
      ? i18n.t("search.more", { total: total, shown: hits.length }) : "";
    $("#hitlist").innerHTML = '<div class="hithead">'
      + esc(i18n.t("search.head", { hits: i18n.t("count.hits", { n: hits.length }),
                                    projects: i18n.t("count.projects", { n: order.length }) }))
      + esc(more) + '<span class="sp"></span>'
      + '<button type="button" id="hit-close">' + esc(i18n.t("search.close")) + "</button></div>"
      + order.map(function (slug) {
          var rows = byProj[slug];
          return '<div class="hitgroup"><h4>' + esc(rows[0].name || slug)
            + '<span class="mono">' + rows.length + "</span></h4>"
            + rows.map(function (h) {
                // 正文没命中时（只命中标题/标签/id）没有片段可截，就说清是哪儿命中的，
                // 别留一行空白让人以为片段加载失败。
                var where = (h.where || []).map(whereLabel).join(" / ");
                var tail = h.body ? esc(U.snippet(h.body, q))
                  : (where ? esc(i18n.t("search.hit.where", { where: where })) : "");
                return '<a class="hit" href="' + projectHref(slug) + "#" + encodeURIComponent(h.id) + '">'
                  + '<span class="hid s-' + esc(h.status) + '">' + esc(h.id) + "</span>"
                  + '<span class="ht">' + esc(h.title || i18n.t("common.untitled")) + "</span>"
                  + '<span class="hd mono">' + esc(h.date || "") + "</span>"
                  + (tail ? '<span class="hs">' + tail + "</span>" : "")
                  + "</a>";
              }).join("")
            + "</div>";
        }).join("");
    var cl = $("#hit-close");
    if (cl) cl.addEventListener("click", hideHits);
  }

  /* -------------------------------------------------------------- 同步状态 */

  /* 数据仓的 git push 是换机器和灾难恢复的全部依据，而它失败起来是完全无声的：
     服务照常返回 201、页面照常刷新，几周后到另一台机器上 git pull 才发现是空的。
     所以这里给一个「平时不吵、出事必见」的指示：正常时只是顶栏一个小箭头（悬停
     能看到最近一次同步了什么），失败时变成一块红色的、点得开、能一键重试的提示。 */
  var GIT = null;
  /* 服务端已经把状态分类过了（state + 人话 summary + 怎么修的 hint），但那两句
     是中文——Python 侧按需求明确不在翻译范围。所以顺序反过来：**本地有这个
     state 的说法就用本地的**（git.state.* / git.hint.*），只有本地不认识的
     state 才退回服务端那句中文。这样英文界面上不会突然冒出一句中文，
     而服务端将来多出一个状态时也不会显示成一个裸英文单词。
     GIT_STATES 这张表只是「本地认识哪些 state」的清单，文案在 i18n 里。 */
  var GIT_STATES = ["disabled", "misconfigured", "idle", "clean", "committed", "pushed", "error"];
  function gitStateText(state) {
    var key = "git.state." + state;
    return i18n.has(key) ? i18n.t(key) : "";
  }
  function gitHintText(g) {
    var key = "git.hint." + (g.state || "");
    return i18n.has(key) ? i18n.t(key) : (g.hint || "");
  }

  function refreshGit() {
    if (MODE !== "server") return Promise.resolve();
    // 专用端点优先；老服务端没有它，退回 /api/status 里的 git 字段。
    return api("/api/git")
      .catch(function () { return api("/api/status").then(function (d) { return d.git; }); })
      .then(function (g) { GIT = g || null; paintGit(); })
      .catch(function () { /* 状态查不到不该弹错——它本身就是个后台指示 */ });
  }

  function gitText() {
    if (!GIT) return "";
    var out = gitStateText(GIT.state) || GIT.summary || GIT.state || "";
    if (GIT.at) out += " " + i18n.t("git.at", { at: GIT.at });
    if (GIT.pending) out += " · " + i18n.t("git.pending", { n: GIT.pending });
    var hint = gitHintText(GIT);
    if (hint) out += "\n" + i18n.t("git.fix", { hint: hint });
    if (GIT.detail) out += "\n" + i18n.t("git.raw", { detail: GIT.detail });
    return out;
  }

  function paintGit() {
    var dot = $("#gitdot"), warn = $("#gitwarn");
    if (!GIT) { dot.hidden = true; warn.hidden = true; return; }
    // ok 由服务端判定（pushed / clean 才算好）。没有 ok 字段的老服务端退回看 state。
    var bad = GIT.ok === undefined ? GIT.state === "error" : !GIT.ok;
    var loud = bad && GIT.state !== "disabled" && GIT.state !== "idle";
    dot.hidden = false;
    dot.className = "gitdot g-" + (GIT.state || "idle");
    dot.title = i18n.t("git.title", { text: gitText() });
    // 只有「本来该同步却没同步成」才吵：没开自动同步、刚起服务还没写过，都不是问题
    warn.hidden = !loud;
    warn.textContent = i18n.t(GIT.state === "error" ? "git.warn.error" : "git.warn.misconfigured");
    warn.title = gitText() + "\n" + i18n.t("git.retry");
  }

  function retrySync() {
    toast(i18n.t("toast.sync.running"));
    api("/api/sync", { method: "POST" }).then(function (r) {
      if (r && r.state) GIT = r;
      paintGit();
      var ok = GIT && (GIT.ok === undefined ? GIT.state !== "error" : GIT.ok);
      var summary = (GIT && (gitStateText(GIT.state) || GIT.summary)) || "";
      toast(i18n.t(ok ? "toast.sync.ok" : "toast.sync.failed", { summary: summary }), !ok);
      return refreshGit();
    }).catch(fail);
  }

  /* -------------------------------------------------------------- 启动 */

  function newProject() {
    var name = prompt(i18n.t("confirm.project.name"));
    if (!name || !name.trim()) return;
    api("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    }).then(function (p) { location.href = projectHref(p.slug); }).catch(fail);
  }

  /* ------------------------------------------------------------ 界面文案
   *
   * index.html 里的静态文案全部由 data-i18n 属性在这里刷进去（选择的理由写在
   * index.html 的注释里）。首屏和切语言走的是**同一个函数**，所以不存在
   * 「首屏对了、切换之后漏了一处」这种半吊子状态——那正是手工接线最容易留下的坑。
   */
  function paintChrome() {
    document.documentElement.lang = uiLang();
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = i18n.t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      el.innerHTML = i18n.tHtml(el.getAttribute("data-i18n-html"));
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.title = i18n.t(el.getAttribute("data-i18n-title"));
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      el.placeholder = i18n.t(el.getAttribute("data-i18n-ph"));
    });
    paintLang();
    paintToken();
    paintScope();
    paintLive();
    paintGit();
  }
  function paintLang() {
    document.querySelectorAll("#langtoggle button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-lang") === uiLang());
    });
  }
  function paintLive() {
    $("#live").title = i18n.t(MODE === "static" ? "app.live.static" : "app.live");
  }

  /* 切语言 = 重画整页。唯一的例外是**正在编辑而且有未保存改动**：那时候重画
     编辑器会把人正在敲的字冲掉，所以只刷顶栏，编辑器保持原样直到它自己结束。
     不脏的编辑器照常重画——那样按钮和提示也一起跟上了新语言。 */
  window.addEventListener(i18n.EVENT, function () {
    var keepEditor = editing && isDirty();
    paintChrome();
    renderProjects();
    if (!PROJECT) { renderHome(); return; }
    renderRows();
    renderDiagram();
    renderFlow();
    renderWarnings();        // 警告栏和缺失横幅现在都是本语言的说法，切换要跟上
    renderMissingPaths();
    applyView();
    if (keepEditor) { renderSelection(); return; }
    onHash();
  });

  paintChrome();
  if (MODE === "static" || !PROJECT) {
    $("#btn-new").hidden = MODE === "static" || !PROJECT;
    $("#btn-token").hidden = MODE === "static";
  }
  /* 「＋ 项目」以前只长在项目索引页上，而只剩一个项目时那个页面会 302 弹回项目页，
     于是网页端永远建不出第二个项目。现在它在顶栏，任何页面都够得着。 */
  $("#btn-newproj").hidden = MODE === "static";
  if (MODE === "static") $("#live").className = "dot";
  $("#lb-close").addEventListener("click", closeLightbox);

  function boot(id) {
    var el = document.getElementById(id);
    var raw = el ? el.textContent.trim() : "";
    try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }

  var pb = boot("projects-data");
  PROJECTS = pb ? (pb.projects || pb) : [];
  renderProjects();

  $("#home").hidden = !!PROJECT;
  $("#main").hidden = !PROJECT;
  document.body.classList.toggle("home-mode", !PROJECT);

  if (PROJECT) {
    var f = boot("forest-data");
    if (f) apply(f); else refresh();
    if (MODE === "server") refreshProjects();
  } else {
    if (MODE === "server") refreshProjects(); else renderHome();
  }

  if (MODE === "server") {
    var es = new EventSource(BASE + "/api/events");
    var seen = -1;
    es.onopen = function () { $("#live").className = "dot live"; };
    es.onerror = function () { $("#live").className = "dot off"; };
    es.onmessage = function (m) {
      var v = JSON.parse(m.data).version;
      if (seen < 0) { seen = v; return; }
      if (v !== seen) {
        seen = v;
        forestCache = Object.create(null);   // 别的项目也可能变了，跨项目搜索的缓存作废
        refreshProjects();
        refreshGit();
        if (PROJECT && !editing) refresh();   // 正在编辑时不要把用户的输入冲掉
      }
    };
    // git 是防抖提交的（默认 45 秒），版本号不变也可能刚刚 push 失败，所以另外轮询。
    refreshGit();
    setInterval(refreshGit, 20000);
  }
})();

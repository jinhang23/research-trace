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

  /* 分叉那两句**人写的散文**：`decision:`（这个岔路口在决定什么）和候选自己那句
     说明。理由和上面那条一模一样——`grep -rn "类别不平衡" projects/` 一秒就答得出
     「当年是在哪个岔路口纠结这件事」，站内搜索答不出就等于比 grep 弱。
     `decision:` 尤其不能漏：候选有谁、选中了谁都算得出来，唯独它只能人写。
     取值（extends / alternative）**故意不收**：那不是散文，收进来搜任何一个词
     都会命中半棵树。判据和 core.fork_haystack、服务端、MCP 是同一份。 */
  function forkHay(step) {
    return [step.decision || "", step.branch_note || ""].filter(Boolean).join(" ");
  }
  /* 这一步**自己写下的**那一行 `chapter:`（名字 + 那句说明）。判据就是 grep：
     `grep -rn 消融实验 projects/` 命中的是声明它的那一个文件，继承来的二十步
     文件里一个「消融」都没有。拿归属（继承来的 name）当判据，搜「消融」会一口气
     命中二十条一模一样的，真正的答案（这条线从哪儿开始）反而被埋掉。
     判据和 core.chapter_haystack、服务端、MCP 是同一份。 */
  function chapterHay(step) {
    var ch = step && step.chapter;
    if (!ch) return "";
    return [ch.declared ? (ch.name || "") : "", ch.note || ""].filter(Boolean).join(" ");
  }
  function hay(step) {
    var tr = (step && step.tr) || {}, extra = "";
    Object.keys(tr).sort().forEach(function (l) {
      var e = tr[l] || {};
      extra += " " + (e.title || "") + " " + (e.name || "") + " " + (e.body || "");
    });
    return (step.id + " " + (step.title || "") + " " + (step.body || "") + " "
            + (step.tags || []).join(" ") + " " + locationsHay(step) + " " + forkHay(step)
            + " " + chapterHay(step) + extra).toLowerCase();
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

  /* -------------------------------------------------------- ①b 拖拽：可测的那一半
   *
   * 拖拽本身是 DOM 的事，在 node 里测不了。但拖拽里真正会**把记录写坏**的判断
   * 一个都不是 DOM：落点合不合法、指针底下压着的是哪一张卡、这一拖到底带走了
   * 哪几步。它们和 moveError 一样剥在这一层，tests/app.test.js 逐条钉住。
   *
   * 合法性**不在这里重判一遍**——界面层直接问 moveError，和「移动」对话框问的
   * 是同一个函数。两套判断迟早会不一致，而不一致的那一刻用户看到的是
   * 「能拖，拖完报错」：手势说可以，服务端说不行，人只会认为这个功能坏了。
   */

  /* 起拖阈值。没有它，每一次「点一下选中这个节点」都可能变成一次意外移动——
     而移动不是撤销一下就没事的操作，它会往 note.md 里追加一条永久审计，
     还会逼人当场编一句原因。5 像素是「手抖」和「我要拖」之间的那条线。 */
  var DRAG_SLOP = 5;
  function beyondSlop(dx, dy, slop) {
    var s = slop === undefined ? DRAG_SLOP : slop;
    return dx * dx + dy * dy >= s * s;
  }

  /* 这一拖带走的是哪一片（含自己）。后端的 move_step 本来就是整棵子树跟着走，
     所以拖动时必须让人看得见那一片：看不见的话，拖一棵二十步的子树和拖一个
     光杆节点在屏幕上长得一模一样，而两者的后果差二十倍。
     沿 parent 反查而不是读 children：children 是服务端派生出来的，这一层
     只依赖每条记录自己写着的那一个字段。 */
  function subtreeIds(byId, id) {
    if (!byId || !byId[id]) return [];
    var kids = Object.create(null);
    Object.keys(byId).forEach(function (k) {
      var p = byId[k].parent || "";
      if (p) (kids[p] || (kids[p] = [])).push(k);
    });
    var out = [], queue = [id], seen = Object.create(null);
    while (queue.length) {
      var cur = queue.shift();
      if (seen[cur]) continue;          // 数据坏成环时也不许把这里转死
      seen[cur] = 1;
      out.push(cur);
      (kids[cur] || []).forEach(function (k) { queue.push(k); });
    }
    return out;
  }

  /* 命中测试。刻意不用 document.elementFromPoint：拖动时指针底下悬着的是跟手的
     小标签，问 DOM 永远只会问到那个标签自己。坐标是布局早就算好的，直接判坐标。
     后画的盖住先画的，所以从后往前找。 */
  function hitRect(rects, x, y) {
    for (var i = (rects || []).length - 1; i >= 0; i--) {
      var r = rects[i];
      if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) return r.id;
    }
    return "";
  }

  /* 指针在不在这块**可视区域**上。

     命中测试是纯坐标换算，而 #diagram / #rows 的矩形在滚动时会伸到视口外去，
     于是「指针停在顶栏的搜索框上」「指针停在右边的详情面板上」照样能换算出
     画布里某个屏幕上根本看不见的节点——松手就是一次挂到看不见的地方的移动，
     而移动会写一条永久的审计记录。所以命中之前先问一句：指针真的在这块上吗。 */
  function withinRect(r, x, y) {
    return !!r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  /* 列表视图的命中：定高行，除一下就是第几行。越界回 -1，不回 0——
     「拖到列表下方的空白」和「拖到第一行」是两件完全不同的事。 */
  function rowAt(y, rowH, count) {
    if (!(rowH > 0) || y < 0) return -1;
    var i = Math.floor(y / rowH);
    return i >= 0 && i < count ? i : -1;
  }

  /* -------------------------------------------------------------- ⑥ 提示级
   *
   * 这三条诊断服务端发的是 warn 级，但它们**不影响 L0–L4**。混在真警告里显示，
   * 人很快就不再看警告栏了——而警告栏里那些真的会降级的条目才是要人动手的。 */
  /* 分叉那四条一起进来：它们同样一格等级都不降。尤其 undecided_fork ——
     它的文案里明写着「同时开几条线是研究的常态，不是错」，把它摆进警告栏
     就是拿一句安抚话去占一个要人动手的位置，人很快连真警告一起不看了。
     decision_without_candidates 是 fork_without_decision 的镜像（写了「在决定
     什么」却一个候选都没标），同样只差一句人写的话，和评级无关。 */
  var HINT_CODES = ["section_without_prose", "table_without_explanation", "code_without_explanation",
                    "lone_alternative", "fork_without_decision",
                    "decision_without_candidates"];
  /* 「还没做决定的岔路口」既不是缺陷也不是写法问题，是**待办**，
     由页面上那条 #forkbar 专门说。它不进警告栏——
     同一件事说两遍会让人以为自己犯了错，而人消除「错误」最省事的办法是
     随手把一条支标成 dead，那是拿假结论换一屏干净的输出。
     trace_cli.py 的 TODO_CODES 是同一条判断的另一半，两边必须一致。 */
  var TODO_CODES = ["undecided_fork"];
  function warnLevel(w) {
    if (w && TODO_CODES.indexOf(w.code) >= 0) return "todo";
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

  /* ==================================================== ⑦ 三种关系的几何
   *
   * 树上的父子边到现在为止只有一副样子，但它其实说着三件不同的事：
   *   延伸    C 接着 A 往下做（绝大多数，**一个像素都不许动**）
   *   互斥候选 A/B 是同一个问题的两个答案，只能选一条走下去
   *   汇回    支线上的产物，被另一条线上更靠后的一步读了（它是 `input:`，不是树边）
   *
   * 规格里「颜色只作线型的补强」这一条按用户的要求放宽了：颜色可以承载信息，
   * **但每一种关系必须再配一个非颜色的通道**——打印成灰度、或者看不见颜色的人，
   * 丢掉的只该是那一眼，不是那个意思。于是：
   *   互斥候选 → 强调色 ＋ 一道把这一组括起来的**弧形括弧**（forkBracket）
   *   汇回     → 第三种颜色 ＋ **曲线加箭头**（rejoinCurve / railRejoin）
   * 曲线这件事本身就说明「它不属于树」：树上的边永远是正交折线，从不弯。
   *
   * 线型仍然只归 status、不透明度仍然只归祖先链/搜索命中，这里一个都没碰。
   *
   * 几何全部剥进这一层是因为它们是最容易写错、又最看不出错的那类代码：
   * 括弧少算一个端点只是「有点歪」，没人会去查，而它正是那半个非颜色通道。
   */

  var BRANCH_ALT = "alternative";

  /* 一组候选在图视图上的括弧。返回 null = 这一组在当前布局里没有可画的位置。
   *
   * 端点取的是**每个候选卡片的水平中点**（和边的落点一致），所以括弧的两头
   * 正好压在最左和最右那两条候选边上，读起来就是「这几条是一组」。
   * 只有一个候选时（core 会报 lone_alternative）括弧照画，宽度给一个最小值：
   * 那一条诊断说的正是「一个候选不成其为选择」，画出来才看得见它孤零零的。
   */
  function forkBracket(nodes, group, opts) {
    opts = opts || {};
    var nw = opts.nw || 176;
    /* lift 有一个上限：层与层之间只有 V_GAP（38px），而这道括弧和它上面那句
       标注都得挤在里面——括弧再往上抬，标注就顶进父节点的卡片里被压住了。
       15 是「箭头（顶边往上 7px）之上留 3px、标注留 14px、再离父卡片 6px」凑出来的。 */
    var lift = opts.lift === undefined ? 15 : opts.lift;   // 括弧离候选顶边多远
    var tick = opts.tick === undefined ? 5 : opts.tick;    // 两端朝下的小竖线
    var labelH = opts.labelH === undefined ? 17 : opts.labelH;
    var xs = [], top = null;
    (group && group.options || []).forEach(function (id) {
      var n = nodes && nodes[id];
      if (!n) return;
      xs.push(n.x + nw / 2);
      if (top === null || n.y < top) top = n.y;
    });
    if (!xs.length || top === null) return null;
    var x1 = Math.min.apply(null, xs), x2 = Math.max.apply(null, xs);
    var half = Math.max((x2 - x1) / 2, nw * 0.18);         // 单个候选也要看得见
    var cx = (x1 + x2) / 2;
    x1 = cx - half; x2 = cx + half;
    var y = top - lift;
    var r = Math.max(2, Math.min(8, half / 2));
    /* 横杠在**不属于这一组的兄弟**头上断开。

       候选和普通延伸挂在同一个父节点底下、而普通那条按 id 序正好排在两个候选之间
       （很常见：019 是候选、019m 是顺手做的清洗、020 又是候选），
       一道连续的横杠会把三张卡片一起圈住，牌子却写着「2 选 1」——
       圈住的和数出来的对不上，而人相信的是自己看见的那一圈。
       所以把横向那一段拆成几截，正对着外人的地方留一个真的口子。
       它是形状，不是颜色：灰度打印下同样看得见「这里跳过去了」。 */
    var runL = x1 + r, runR = x2 - r;
    var cuts = (opts.skip || [])
      .filter(function (x) { return x > runL + 6 && x < runR - 6; })
      .sort(function (a, b) { return a - b; });
    var gap = Math.min(11, Math.max(6, nw * 0.06));
    var segs = [], at = runL;
    cuts.forEach(function (x) {
      if (x - gap > at) segs.push([at, x - gap]);
      at = Math.max(at, x + gap);
    });
    segs.push([at, runR]);

    var d = "M" + x1 + " " + (y + tick)
      + "V" + (y + r)
      + "Q" + x1 + " " + y + " " + runL + " " + y;
    segs.forEach(function (s, i) {
      d += (i ? "M" + s[0] + " " + y : "") + "H" + s[1];
    });
    d += "M" + runR + " " + y
      + "Q" + x2 + " " + y + " " + x2 + " " + (y + r)
      + "V" + (y + tick);

    /* 三个以上候选时，中间那几个头上各落一个钩——数钩子就是数候选。 */
    xs.sort(function (a, b) { return a - b; });
    xs.forEach(function (x) {
      if (x <= runL || x >= runR) return;                   // 两端已经有钩了
      d += "M" + x + " " + y + "V" + (y + tick);
    });
    /* 根之间也能成一组（两种互斥的开局）。它上面没有节点，括弧顶到画布外面去了，
       标注就没有地方摆在上方——这时候把标注挪到括弧右边，而不是让它被裁掉。 */
    return { d: d, x1: x1, x2: x2, y: y, cx: cx, side: y - labelH < 0 };
  }

  /* 括弧旁边那句话说什么。三态一一对应，**不许**把 abandoned 说成错误：
     「这个问题分出去的路全都走到了 dead」是一条结论（P4），不是一个待填的窟窿。 */
  function forkLabel(group) {
    if (!group) return null;
    // 只有一个候选的「分叉」根本没做过选择——说它「已定」是在替人宣布一件没发生的事。
    // core 的 state 只数还活着的候选，1 个活的就叫 decided，对 2 选 1 是对的，
    // 对「从头到尾只有 1 条」是错的。CLI、MCP、以及本页顶上的提示栏都说
    // 「只有一个候选（还不成其为选择）」，只有这块牌子说「已定」，页面自己打架。
    if ((group.options || []).length < 2) {
      return { key: "decision.lone", title: "decision.lone.title",
               vars: { id: (group.options || [])[0] || "" }, state: "lone" };
    }
    if (group.state === "decided") {
      return { key: "decision.settled", title: "decision.settled.title",
               vars: { id: group.chosen }, state: "decided" };
    }
    if (group.state === "abandoned") {
      return { key: "decision.alldead", title: "decision.alldead.title",
               vars: {}, state: "abandoned" };
    }
    // 未决：N 是**还活着**的条数，不是候选总数——已经放弃掉的那几条不再是选项。
    return { key: "decision.pick", title: "decision.pick.title",
             vars: { n: (group.live || []).length }, state: "open" };
  }

  /* 一条曲线加一个箭头。控制点只在**水平**方向伸出去，于是无论两端怎么摆，
     画出来都是一条 S 形——和树上那种「竖-横-竖」的正交折线在形状上永不重合，
     这就是汇回的那半个非颜色通道。 */
  function curveBetween(p1, p2, bow, head) {
    head = head === undefined ? 7 : head;
    // 两端同一行时，只往水平方向伸控制点会让四个点的 y 全相等——
    // 画出来是一条**笔直**的横线，和普通的短正交边一模一样，
    // 「曲线」这半个非颜色通道当场消失（灰度下就只剩卡片上的字形能救）。
    // 而"生产者和消费者在同一层"恰恰是最常见的汇回形状。
    // 所以同行时把弓弦转到**垂直**方向：拱起来的那一下就是形状本身。
    var flat = Math.abs(p2.y - p1.y) < 1;
    var lift = flat ? Math.max(14, Math.min(34, Math.abs(p2.x - p1.x) * 0.45)) : 0;
    var c1 = { x: p1.x + bow, y: p1.y - lift }, c2 = { x: p2.x - bow, y: p2.y - lift };
    var d = "M" + p1.x + " " + p1.y
      + "C" + c1.x + " " + c1.y + " " + c2.x + " " + c2.y + " " + p2.x + " " + p2.y;
    // 箭头指的是「谁汇进谁」。方向取末端那一小段的走向，而它恒为水平（控制点
    // 只在水平方向伸），所以只用判正负，不用算角度。
    var s = bow >= 0 ? 1 : -1;
    var back = p2.x - s * head;
    var arrow = "M" + back + " " + (p2.y - 4) + "L" + back + " " + (p2.y + 4)
      + "L" + p2.x + " " + p2.y + "Z";
    return { d: d, arrow: arrow, from: p1, to: p2 };
  }

  /* 图视图上的一条汇回边：从生产者卡片的侧边出发，扎进消费者卡片的侧边。
     走侧边而不是上下边，是因为汇回的两端按定义分属两条支线——它们在水平方向
     一定是分开的，而上下边已经被树边占满了（再叠上去就分不清哪条是父子）。 */
  function rejoinCurve(a, b, opts) {
    if (!a || !b) return null;
    opts = opts || {};
    var nw = opts.nw || 176, nh = opts.nh || 58;
    var right = (b.x + nw / 2) >= (a.x + nw / 2);
    var p1 = { x: right ? a.x + nw : a.x, y: a.y + nh / 2 };
    var p2 = { x: right ? b.x : b.x + nw, y: b.y + nh / 2 };
    var dx = Math.abs(p2.x - p1.x);
    var bow = Math.max(28, Math.min(130, dx * 0.45)) * (right ? 1 : -1);
    return curveBetween(p1, p2, bow);
  }

  /* 列表视图（轨道图）上的汇回。git graph 的语汇里，横过来的那条线就是这么画的，
     所以这里不套图视图那一套，而是让曲线从右边的空档绕过去——轨道之间的竖线是
     满的，从中间穿会和它们缠在一起。outX 由调用方给（轨道图右边专门留出来的
     那条空档），同一张图上多条汇回各让开一点，免得叠成一条。 */
  function railRejoin(p1, p2, outX, opts) {
    opts = opts || {};
    var gap = opts.gap === undefined ? 5.5 : opts.gap;   // 箭头不要压在节点点上
    var end = { x: p2.x + gap, y: p2.y };
    var c1 = { x: outX, y: p1.y }, c2 = { x: outX, y: p2.y };
    var d = "M" + (p1.x + gap) + " " + p1.y
      + "C" + c1.x + " " + c1.y + " " + c2.x + " " + c2.y + " " + end.x + " " + end.y;
    var head = opts.head === undefined ? 6 : opts.head;
    var arrow = "M" + (end.x + head) + " " + (end.y - 3.6)
      + "L" + (end.x + head) + " " + (end.y + 3.6)
      + "L" + end.x + " " + end.y + "Z";
    return { d: d, arrow: arrow };
  }

  /* 一条汇回边跟当前选中的那一步有没有关系。
   *
   * **不能**沿用树边那条「两端都在祖先链上」的判据：汇回按定义就是两端分属两条
   * 支线，那个判据下它永远为假——一选中任何节点，所有汇回边就集体淡掉，等于
   * 这个功能在选中状态下不存在。这里问的是同一件事的正确版本：这条边碰没碰到
   * 选中的那条链。通道没变，仍然是不透明度承载「和选中有没有关系」。 */
  function rejoinRelated(m, sel, chain) {
    if (!sel) return true;
    if (!m) return false;
    return !!(chain && (chain[m.from] || chain[m.to])) || m.from === sel || m.to === sel;
  }

  /* 这一步是哪一组候选里的。组是**派生**的，磁盘上只有它自己那句 branch:，
     所以这里也只是去现成的 branch_groups 里按分叉点找，绝不另存一份归属。
     根之间那一组的 at 是空串（core 的约定）。 */
  function groupOf(groups, step) {
    if (!step || step.branch !== BRANCH_ALT) return null;
    var at = step.parent || "";
    var got = null;
    (groups || []).forEach(function (g) {
      if (g.at === at && (g.options || []).indexOf(step.id) >= 0) got = g;
    });
    return got;
  }

  /* ================================================ ⑧ 定稿流程：第二样东西
   *
   * 开发路径 = 现在这棵树的**全部**（含走不通的、含还悬着的岔路口），给自己查问题用。
   * 定稿流程 = 真正把成果做出来的**那条链**，给别人照着做、给论文 Methods 用。
   *
   * 它**不是第四种画法**。图 / 列表 / 数据流是同一批步骤的三张图；定稿流程画的是
   * 另一批步骤（trace_core 从 `result:` 沿 `input:` 反向做闭包算出来的子图）。
   * 把它塞进同一排按钮，等于对读者说「这是同一件事的另一种排版」——而那正是
   * i18n 的 pipeline.pair.note 专门写来挡的那个误解。
   *
   * 这一层做两件事，一件都不碰 DOM：
   *   1) 把 forest.pipeline 整理成一份模型（pipelineModel），供这一屏渲染；
   *   2) 从正文里取出「做了什么」（sectionOf）。
   *
   * **三样导出（SVG 图 / Methods 草稿 / 独立页面）在这里一份都没有，这是有意的。**
   * 它们只有**一份**实现，在 Python 那一侧（trace_mcp 的 pipeline_svg /
   * pipeline_methods / pipeline_page），CLI、REST、MCP、静态导出全走它。
   * 这一页拿到的是那份实现的产物：服务模式下 fetch `/api/p/{项目}/pipeline/*`，
   * 静态模式下读 `build` 灌进来的同一批字节。
   *
   * 曾经这里有第二份（JS 各画一遍 SVG 和 markdown）。两份实现看起来都对，
   * 输出却是两份不同的文件：屏幕上讨论的是一张图，投出去的是另一张，
   * 而**其中一份会进论文**。谁的排版更好看不是这条规矩要解决的问题——
   * 「只有一份」本身才是。
   */

  /* 正文那五个小节的**语义键**，顺序逐字对着 trace_core.SECTION_NAMES。
     这里存的是英文键名（why/what/…），不是小节标题——标题是封闭词表里的中文/英文，
     由 i18n 的 template.body 提供（和 langByHeadings 走同一张表）。
     tests/test_web.py 拿 trace_core 逐字核过这个顺序：错一位，Methods 草稿里
     「做了什么」就会变成「为什么」，而那正是定稿流程唯一不该说的东西。 */
  var SECTION_ORDER = ["why", "what", "result", "conclusion", "next"];

  function headingList(body) {
    var out = [];
    String(body == null ? "" : body).split("\n").forEach(function (line) {
      var m = HEADING_RE.exec(line);
      if (m) out.push(m[1]);
    });
    return out;
  }

  /* 取出正文里某一节的内容。**只查表，不猜语种**：先用 langByHeadings 认出这份
     正文用的是哪一套小节名，再按 SECTION_ORDER 的下标去那一套里取标题。
     一节的内容包含它下面所有更深的标题（和 trace_core.sections() 同一套层级语义），
     所以 `### 细节` 不会把「做了什么」切断。 */
  function sectionOf(body, templates, key) {
    var at = SECTION_ORDER.indexOf(key);
    if (at < 0) return "";
    var l = langByHeadings(body, templates);
    if (!l) return "";
    var want = headingList((templates || {})[l] || "")[at];
    if (!want) return "";
    var lines = String(body == null ? "" : body).split("\n");
    var out = [], on = false, lv = 0;
    for (var i = 0; i < lines.length; i++) {
      var m = HEADING_RE.exec(lines[i]);
      if (m) {
        var d = (/^\s*(#{1,6})/.exec(lines[i]))[1].length;
        if (on && d <= lv) break;
        if (!on && m[1].trim() === want) { on = true; lv = d; continue; }
      }
      if (on) out.push(lines[i]);
    }
    return out.join("\n").replace(/^\n+|\n+$/g, "");
  }

  /* forest.pipeline → 三个出口共用的一份模型。
   *
   * `forest.pipeline` **只在项目声明了 `result:` 时才存在**（现存项目必须完全无感），
   * 所以第一件事是老实返回 declared:false，让界面走空态——而不是造一个空流程
   * 假装什么都算过了。
   *
   * opts 里那三个函数把「语言」挡在这一层之外：标题要跟界面语言走、「做了什么」
   * 要按内容语言取、「凭什么在流程里」是一句 i18n 文案。纯函数层持有界面文案，
   * 就等于让它替界面决定说哪种语言（和文件头那条规矩同一条）。 */
  function pipelineModel(forest, opts) {
    opts = opts || {};
    var title = opts.title || function (s) { return (s && s.title) || ""; };
    var what = opts.what || function () { return ""; };
    var whyText = opts.whyText || function () { return ""; };
    var P = (forest && forest.pipeline) || null;
    var byId = Object.create(null);
    ((forest && forest.steps) || []).forEach(function (s) { byId[s.id] = s; });
    var empty = {
      declared: false, order: [], steps: [], edges: [], results: [], why: {},
      levels: {}, level: "", weakest: "", weak: [], dead: [], included: [], excluded: [],
      diagnostics: (P && P.diagnostics) || [], chapters: [],
    };
    if (!P || !P.declared) return empty;
    var noteOf = Object.create(null);
    (P.results || []).forEach(function (r) { noteOf[r.step] = r.note || ""; });
    var levels = P.levels || {}, why = P.why || {};
    var steps = (P.order || []).map(function (id, i) {
      var s = byId[id] || { id: id };
      var w = why[id] || { kind: "", id: "" };
      return {
        id: id, index: i, n: i + 1, step: s,
        title: title(s),
        level: levels[id] || "",
        why: w, whyText: whyText(w),
        result: id in noteOf,
        resultNote: noteOf[id] || "",
        what: what(s),
      };
    });
    return {
      declared: true,
      order: (P.order || []).slice(),
      steps: steps,
      edges: (P.edges || []).map(function (e) {
        return { from: e.from, to: e.to, kind: e.kind || "",
                 via: (e.via || []).slice(), notes: (e.notes || []).slice() };
      }),
      results: (P.results || []).map(function (r) {
        return { step: r.step, note: r.note || "", members: (r.members || []).slice() };
      }),
      why: why, levels: levels,
      level: P.level || "", weakest: P.weakest || "",
      weak: (P.weak || []).slice(), dead: (P.dead || []).slice(),
      included: (P.included || []).slice(), excluded: (P.excluded || []).slice(),
      diagnostics: P.diagnostics || [],
      /* 每个章节各一条定稿流程。**core 已经把那一张 DAG 切好了**（一条 `result:`
         指的那一步在哪一章，这条流程就属于哪一章），这里一步闭包都不重算——
         各算一遍就会出现「屏幕上讨论的图和投出去的图不是一张」。
         `chapters` 只在项目真有章节时才被 core 放进来，没有就是空数组。 */
      chapters: (P.chapters || []).map(function (g) {
        return { name: g.name || "", results: (g.results || []).slice(),
                 order: (g.order || []).slice(), external: (g.external || []).slice(),
                 level: g.level || "", weakest: g.weakest || "",
                 weak: (g.weak || []).slice(), dead: (g.dead || []).slice() };
      }),
    };
  }

  /* ================================================ ⑨ 章节：项目内部并列的几块
   *
   * 主实验 / 消融实验 / 数据准备。磁盘上只多一行 `chapter: 消融实验 | 说明`，
   * 写在**开启那条线的那一步**上，底下整棵子树沿 parent 继承（core.resolve_chapters）。
   *
   * 这一层只做三件不碰 DOM 的事：把 forest.chapters 整理成一份模型、算章节在图上
   * 那几块底色带的坐标、以及「换一次章节会带走底下哪几步」。
   *
   * **归属只有一份判据**，就是服务端给的 `step.chapter.name`（core 算好的继承结果）。
   * 这里绝不拿「这一步自己写没写 chapter:」当归属判断——那是 `.declared`，
   * 混用会让继承来的二十步集体看着像未分章。
   *
   * 界面上章节**不是第三级视图**，它是横切的：开发路径要能按章节看，定稿流程也要，
   * 所以它的入口在顶栏（和搜索并排），而不是在「图/列表/数据流」那一排里再加一个。
   * 理由和 index.html 里 #modeswitch 那段注释同源——一级选**看哪一份东西**，
   * 二级选**怎么画**，章节两者都不改，它改的是**看多大范围**。
   */

  /* 章节在图上用的是一条**新通道**：一块底色带 + 卡片左侧一道色条。
     线型仍然只归 status、不透明度仍然只归祖先链/搜索/章节筛选（同一个语义：
     「和你此刻的关注有没有关系」）、边的颜色仍然只归三种关系。
     六个色相循环用：再多人也分不清，而分不清的颜色等于没有颜色；带子上永远
     跟着章节名（图上是入口处那块牌子，卡片上是 tooltip），色相只负责一眼扫到。 */
  var CHAPTER_HUES = 6;
  function chapterHue(i) {
    var n = Math.floor(Number(i));
    if (!isFinite(n) || n < 0) n = 0;
    return n % CHAPTER_HUES;
  }

  /* forest.chapters → 界面共用的一份模型。
     `forest.chapters` **只在真有人写过 `chapter:` 时才存在**（现存项目完全无感），
     所以第一件事是老实返回 declared:false，让界面一个像素都不渲染——顶栏那个
     筛选器、章节面板、按章节导出全都不该在没有章节的项目里冒出来。 */
  function chapterModel(forest) {
    var C = (forest && forest.chapters) || null;
    var out = { declared: false, list: [], of: Object.create(null), unassigned: [],
                crossings: [], diagnostics: [], byName: Object.create(null) };
    if (!C || !C.declared) return out;
    out.declared = true;
    out.list = (C.chapters || []).map(function (c, i) {
      /* `parts` 是 core 按名字里的斜杠拆好的。**不在这边自己 split**：分隔符的
         唯一来源是 core.CHAPTER_SEP，这里再写一个 "/" 就是第二份声明。
         而且它只影响显示分组——章节不嵌套，底下仍然是平的一层名字。 */
      var parts = (c.parts && c.parts.length ? c.parts : [c.name || ""]).slice();
      var st = c.status || {};
      var e = {
        name: c.name || "", parts: parts,
        group: parts.length > 1 ? parts[0] : "",
        note: c.note || "",
        declaredAt: (c.declared_at || []).slice(),
        steps: (c.steps || []).slice(),
        roots: (c.roots || []).slice(),
        n: c.n === undefined ? (c.steps || []).length : c.n,
        status: { done: st.done || 0, wip: st.wip || 0, dead: st.dead || 0 },
        level: c.level || "", weakest: c.weakest || "",
        index: i, hue: chapterHue(i),
      };
      out.byName[e.name] = e;
      return e;
    });
    Object.keys(C.of || {}).forEach(function (k) { out.of[k] = C.of[k]; });
    out.unassigned = (C.unassigned || []).slice();
    out.crossings = (C.crossings || []).map(function (x) {
      return { from: x.from, to: x.to, kind: x.kind || "",
               from_chapter: x.from_chapter || "", to_chapter: x.to_chapter || "",
               note: x.note || "" };
    });
    out.diagnostics = (C.diagnostics || []).slice();
    return out;
  }

  /* 这一步的章节是**从哪一步继承来的**：沿 parent 往上找第一个自己写了 `chapter:`
     的祖先（可能就是它自己）。人要知道「改哪一步才能改整条线」，而那个答案只有
     一个——声明的那一步。找不到（未分章）就回空串。防环。 */
  function chapterSourceOf(byId, id) {
    var cur = id, seen = Object.create(null);
    while (cur && byId[cur] && !seen[cur]) {
      seen[cur] = 1;
      var ch = byId[cur].chapter;
      if (ch && ch.declared) return cur;
      cur = byId[cur].parent;
    }
    return "";
  }

  /* 在这一步上开一个章节，会**带走底下哪几步**。
     沿子树往下走，遇到自己声明过章节的那一步就整支停住——它和它底下的子树
     早就不听这条线的了。用户吃过的亏是「二十步集体转章，diff 里只有一行」，
     所以这个数得在 toast 里说出来（toast.chapter.carry）。 */
  function chapterCarry(byId, id) {
    var kids = Object.create(null);
    Object.keys(byId || {}).forEach(function (k) {
      var p = byId[k] && byId[k].parent;
      if (p) (kids[p] || (kids[p] = [])).push(k);
    });
    var out = [], seen = Object.create(null), stack = (kids[id] || []).slice();
    while (stack.length) {
      var cur = stack.shift();
      if (seen[cur]) continue;
      seen[cur] = 1;
      var s = byId[cur];
      if (!s) continue;
      if (s.chapter && s.chapter.declared) continue;   // 它自己开了一章，整支不跟着走
      out.push(cur);
      (kids[cur] || []).forEach(function (k) { stack.push(k); });
    }
    return out.sort();
  }

  /* 那句章节说明**写在哪一步身上**（要改它就得 PATCH 那一步）。
     和 core 的裁决逐字一致：id 序最早的那个**带说明的**声明生效；一句说明都没有
     时落在最早的那个声明上。不一致的话，人在界面上改了说明，屏幕上显示的还是
     另一步写的那句——而那正是「双真相源」最气人的形状。 */
  function chapterNoteHome(entry, byId) {
    var ids = (entry && entry.declaredAt) || [];
    for (var i = 0; i < ids.length; i++) {
      var s = byId[ids[i]];
      if (s && s.chapter && s.chapter.note) return ids[i];
    }
    return ids[0] || "";
  }

  /* 图视图上那几块章节底色带。
   *
   * 一个章节可以横跨好几棵树，所以不能简单地按位置圈一个方框——**一个方框里
   * 只要混进一张别的章节的卡片，这块底色就是在撒谎**。所以按层切：每一层里
   * 连续的成员算一段，中间夹进一张外人就断开。于是带子覆盖的**只可能是成员**。
   * 布局一个数都没动（core 的 tree.nodes 原样用），章节只是画在底下的一层。
   */
  function chapterBands(nodes, ids, opts) {
    opts = opts || {};
    var nw = opts.nw || 176, nh = opts.nh || 58;
    var pad = opts.pad === undefined ? 5 : opts.pad;
    var mine = Object.create(null);
    (ids || []).forEach(function (id) { mine[id] = 1; });
    var rows = Object.create(null);
    Object.keys(nodes || {}).forEach(function (id) {
      var n = nodes[id];
      if (!n) return;
      (rows[n.y] || (rows[n.y] = [])).push({ id: id, x: n.x, y: n.y });
    });
    var out = [];
    Object.keys(rows).sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (k) {
      var row = rows[k].sort(function (a, b) {
        return a.x - b.x || (a.id < b.id ? -1 : (a.id > b.id ? 1 : 0));
      });
      var run = null;
      row.forEach(function (n) {
        if (mine[n.id]) {
          if (run) run.x2 = n.x + nw;
          else run = { x1: n.x, x2: n.x + nw, y: n.y };
        } else if (run) { out.push(run); run = null; }
      });
      if (run) out.push(run);
    });
    return out.map(function (r) {
      return { x: r.x1 - pad, y: r.y - pad, w: r.x2 - r.x1 + pad * 2, h: nh + pad * 2 };
    });
  }

  /* 跨章节那条边上的记号：一对 45° 的小斜杠，画在边的中点上（电路图里「这条线
     跨过去了」用的就是这个记号）。
     它是**又一个新通道**——线型仍然归 status、颜色仍然归三种关系、不透明度仍然
     归「和选中有没有关系」。这条边不该藏起来：消融吃着主实验的产物，那正是
     整个项目里最有话说的一条边。 */
  function crossTick(a, b, opts) {
    opts = opts || {};
    var len = opts.len === undefined ? 4.5 : opts.len;
    var gap = opts.gap === undefined ? 3.4 : opts.gap;
    var dx = b.x - a.x, dy = b.y - a.y;
    var m = Math.sqrt(dx * dx + dy * dy) || 1;
    var ux = dx / m, uy = dy / m;
    var sx = ux - uy, sy = uy + ux;                 // 沿边方向转 45°
    var sm = Math.sqrt(sx * sx + sy * sy) || 1;
    sx /= sm; sy /= sm;
    var cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
    var r2 = function (v) { return Math.round(v * 100) / 100; };
    var one = function (off) {
      var px = cx + ux * off, py = cy + uy * off;
      return "M" + r2(px - sx * len) + " " + r2(py - sy * len)
           + "L" + r2(px + sx * len) + " " + r2(py + sy * len);
    };
    return one(-gap / 2) + one(gap / 2);
  }

  /* 章节名**不是路径安全的**：`主实验/数据准备` 是合法名字（设计要求按 `/` 分组
     显示），`CON` 也是。所以按章节导出的文件名必须**派生**出来，绝不能拿名字直接
     拼——这一份逐字对着 trace_write.slugify（NFKC + 非字词字符压成 `-` + 40 字上限），
     外加 Windows 设备名那一道（`con.svg` 打开的是设备不是文件）。 */
  var WIN_RESERVED = ["con", "prn", "aux", "nul",
                      "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
                      "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"];
  var MAX_SLUG = 40;
  function chapterSlug(name) {
    var s = String(name == null ? "" : name);
    if (s.normalize) s = s.normalize("NFKC");
    s = s.trim().toLowerCase()
      .replace(/[^\p{L}\p{N}_]+/gu, "-")
      .replace(/^-+|-+$/g, "")
      .replace(/-{2,}/g, "-");
    if (s.length > MAX_SLUG) s = s.slice(0, MAX_SLUG).replace(/-+$/, "");
    /* 下面这两行的取值**逐字对着 trace_mcp.chapter_export_name**（未分章那一组是
       `unassigned`，Windows 设备名后面补 `-ch`）。两侧各起各的名字的话，同一份
       导出在浏览器里下载下来叫一个名、`build` 写到磁盘上叫另一个名，而那两份
       字节是同一个纯函数的同一份输出——tests/test_seams_chapter.py 拿一张
       名字表逐个比对着这一条。 */
    if (!s) return "unassigned";                    // 未分章那一组也要有自己的文件名
    return WIN_RESERVED.indexOf(s) >= 0 ? s + "-ch" : s;
  }
  /* 服务端**自己说**它刚才编的是哪一章。`?chapter=` 那条路由的回执里，`chapter`
     是一个对象（`{name, label, external, known, no_result}`），不是一个字符串——
     直接拿整个对象和名字比永远不相等，而症状是按章节导出整块静默消失。
     没有这个键（还没升级的服务端会忽略查询参数、回整个项目那一份）就回 null，
     调用方据此降级到整项目那三样，而不是挂一个名不副实的文件名。 */
  function chapterEcho(payload) {
    var ch = payload && payload.chapter;
    return ch && typeof ch.name === "string" ? ch.name : null;
  }
  /* 一批章节名 → 一批**互不相同**的文件名词干。大小写是故意不折叠的（`Ablation`
     和 `ablation` 是两个章节，core 的 chapter_near_duplicate 专门逮它），于是两个
     不同章节完全可能 slug 成同一个名字——用它在清单里的顺序号消歧，绝不让后一份
     静默盖掉前一份。 */
  function chapterFileStems(names) {
    var used = Object.create(null), out = [];
    (names || []).forEach(function (nm, i) {
      var s = chapterSlug(nm), pick = s, k = i + 1;
      while (used[pick]) { pick = s + "-" + k; k++; }
      used[pick] = 1;
      out.push(pick);
    });
    return out;
  }

  /* 某个章节那条定稿流程上的步骤条目。用的是**整份模型里现成的那些条目**，
     不重新算一遍：core 保证每章的 order 是全局 order 的子序列，所以同一步在总图
     和分章图里位置一致，编号也就还是那一个——屏幕上的 [3]、开发路径卡片上的 [3]、
     导出那张图上的 3 必须是同一个数。 */
  function pipelineChapterSteps(model, group) {
    var want = Object.create(null);
    ((group && group.order) || []).forEach(function (id) { want[id] = 1; });
    return ((model && model.steps) || []).filter(function (it) { return want[it.id]; });
  }

  var U = {
    LEVELS: LEVELS, REPRO_STATES: REPRO_STATES, INSIGHT_HEADINGS: INSIGHT_HEADINGS,
    INSIGHT_KIND_BY_HEADING: INSIGHT_KIND_BY_HEADING, SUPERSEDE_WORDS: SUPERSEDE_WORDS,
    PATH_ROLES: PATH_ROLES, HINT_CODES: HINT_CODES,
    levelIndex: levelIndex, splitSections: splitSections,
    splitInsightBody: splitInsightBody, foreignHeadings: foreignHeadings,
    draftKey: draftKey, matches: matches, snippet: snippet, locationsHay: locationsHay,
    pickLang: pickLang, headingsIn: headingsIn, langByHeadings: langByHeadings,
    forkHay: forkHay, chapterHay: chapterHay,
    sizeUnit: sizeUnit, formatPath: formatPath, formatInput: formatInput, formatCode: formatCode,
    MEASURED_ATTRS: MEASURED_ATTRS, inheritPath: inheritPath,
    parseInsights: parseInsights, parseInsightLine: parseInsightLine,
    moveError: moveError, warnLevel: warnLevel,
    DRAG_SLOP: DRAG_SLOP, beyondSlop: beyondSlop, subtreeIds: subtreeIds,
    hitRect: hitRect, rowAt: rowAt, withinRect: withinRect,
    flowLayout: flowLayout, depClosure: depClosure,
    BRANCH_ALT: BRANCH_ALT,
    forkBracket: forkBracket, forkLabel: forkLabel, curveBetween: curveBetween,
    rejoinCurve: rejoinCurve, railRejoin: railRejoin,
    rejoinRelated: rejoinRelated, groupOf: groupOf,
    SECTION_ORDER: SECTION_ORDER, headingList: headingList, sectionOf: sectionOf,
    pipelineModel: pipelineModel, pipelineChapterSteps: pipelineChapterSteps,
    CHAPTER_HUES: CHAPTER_HUES, chapterHue: chapterHue, chapterModel: chapterModel,
    chapterSourceOf: chapterSourceOf, chapterCarry: chapterCarry,
    chapterNoteHome: chapterNoteHome, chapterBands: chapterBands, crossTick: crossTick,
    chapterSlug: chapterSlug, chapterFileStems: chapterFileStems,
    chapterEcho: chapterEcho,
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
  /* 编译了第几次。定稿流程那张图是**服务端画的**，取回来之后得知道手上这份对不对
     得上当前的记录 —— 一张过期的方法图会被当成现在的方法图，那比没有图糟。
     用它而不是 forest 里的版本号：`/forest` 的响应里没有版本这一项，拿 undefined
     当版本会让缓存永远命中，于是改完一步图再也不更新。 */
  var FOREST_SEQ = 0;
  var PROJECTS = [];
  /* ⑨ 章节。`forest.chapters` 只在项目里真有人写过 `chapter:` 时才存在，
     没有时这份模型的 declared 是 false，界面上一个像素都不多画。 */
  var CH = U.chapterModel(null);
  /* 顶栏那个章节筛选器选中的是谁。空串 = 全部；CHAP_NONE = 只看未分章的那些。
     **不记进 localStorage**：它和搜索是同一类动作（缩小注意力范围），而一个
     记住了的筛选器会让人下次打开时对着半屏淡掉的节点找原因——mode / view 记得住
     是因为它们换的是「看哪一份东西」，不是「看多大范围」。
     哨兵值里那个竖线是**故意**的：写入侧拒收带竖线的章节名（那一行的语法就是
     「名字 | 说明」），所以它是一个真章节名永远不可能长成的样子。 */
  var CHAP_NONE = "|none";
  /* 未分章那一组在 `?chapter=` 上的记号。**逐字就是 trace_mcp.CHAPTER_NONE**
     （tests/test_seams_chapter.py 直接读那个常量比着这一行）：核心把未分章那组的
     名字定成空串，而空串在查询串上和「没给」长得一模一样，可它常常就是主实验。
     和上面那个哨兵是两回事——上面那个只活在这一页里（所以敢用竖线），
     这一个要发到服务端去，两边必须是同一个字节。 */
  var CHAP_SENT = "-";
  var chapFilter = "";
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
  /* 看的是**哪一份东西**：开发路径（这棵树的全部）还是定稿流程（产出成果的那条链）。
     它和 view 是两级，不是四选一——理由写在 index.html 的 #modeswitch 那段注释里。
     默认永远是开发路径：绝大多数项目一个 `result:` 都没声明，把人直接丢进一个
     空态页面，等于让这个功能的第一印象是「这里什么都没有」。 */
  var MODES = ["dev", "pipeline"];
  var savedMode = localStorage.getItem("trace.mode");
  var mode = MODES.indexOf(savedMode) >= 0 ? savedMode : "dev";
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

  /* 项目卡片上那一行章节：分了几章、各几步。
   *
   * 索引页拿到的是 `/api/projects` 那一份，里面**没有** forest，所以这一行只能由
   * 服务端顺手带上（`chapters: [{name, n}]`，没有章节的项目整个键不出现——
   * 那些项目的卡片必须逐字节和从前一样）。带上了就显示，没带就一个字都不多，
   * 于是这一页对着还没升级的服务端也不会显示一行「0 个章节」的谎话。 */
  function projectChapters(p) {
    var cs = (p && p.chapters) || [];
    if (!cs.length) return "";
    return '<div class="pchaps mono" title="' + esc(i18n.t("chapter.badge.title")) + '">'
      + esc(i18n.t("count.chapters", { n: cs.length })) + " · "
      + cs.map(function (c) {
          return esc(i18n.t("chapter.entry.bare", { chapter: c.name || i18n.t("chapter.none") })
            + " " + i18n.t("count.steps", { n: c.n || 0 }));
        }).join(" · ")
      + "</div>";
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
        + projectChapters(p)
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
    FOREST_SEQ++;                       // 记录变了 —— 那张图得重新去取，缓存的那份已经过期（见 fetchFigure）
    IDX = Object.create(null);
    F.steps.forEach(function (s) { IDX[s.id] = s; });
    CH = U.chapterModel(F);
    // 筛的那个章节可能刚被改名/删空了。留着一个不存在的筛选值，屏幕上就是
    // 满屏淡掉的节点而没有任何一个亮的——那看着像功能坏了。
    if (chapFilter && chapFilter !== CHAP_NONE && !CH.byName[chapFilter]) chapFilter = "";
    if (!CH.declared) chapFilter = "";
    chapStems();
    renderChapFilter();
    document.documentElement.style.setProperty("--row-h", (F.row_h || 28) + "px");
    renderRails();
    renderRows();
    renderDiagram();
    renderFlow();
    renderWarnings();
    renderMissingPaths();
    renderForks();
    renderPipeline();
    applyView();
    onHash();
  }
  function refresh() { return papi("/forest").then(apply).catch(fail); }
  function refreshProjects() {
    return api("/api/projects").then(function (d) {
      PROJECTS = d.projects || [];
      renderProjects();
      if (!PROJECT) { renderHome(); return; }
      // 这一屏里也用得上项目名（面包屑、空态），而项目名在 PROJECTS 里、比 forest
      // 晚到。图本身的抬头不再受这个影响——它由服务端画，服务端手上一直有显示名。
      if (!editing) renderPipeline();
      // 洞察面板是从 PROJECTS 里读的，而它比 forest 晚到。
      // 不在这里重画一次的话，第一次打开项目看到的就是个空框。
      if (!selected() && !editing) renderDetail();
    }).catch(function () {});
  }

  /* -------------------------------------------------------------- ⑨ 章节筛选器
   *
   * 它长在顶栏、挨着搜索框，**不在**「图 / 列表 / 数据流」那一排里，也不在
   * 「开发路径 / 定稿流程」那一排里。理由是层次本来就是这样的：那两排一个选
   * 「看哪一份东西」、一个选「怎么画」，而章节两者都不改——它选的是**看多大范围**，
   * 横切在这两级之上。所以开发路径按章节看、定稿流程按章节编，用的是同一个控件。
   *
   * 一个 `chapter:` 都没写的项目里它**整个不渲染**：多一个恒灰的下拉框就已经不是
   * 「完全无感」了。
   */
  function renderChapFilter() {
    var box = $("#chapfilter");
    if (!box) return;
    box.hidden = !CH.declared;
    // 图例里那两条（底色带 / 跨章记号）跟着一起：没有章节的项目图例上一个字都不多。
    var lg = $("#chaplegend");
    if (lg) lg.hidden = !CH.declared;
    if (!CH.declared) { box.innerHTML = ""; return; }
    box.title = i18n.t("app.chapter.title");
    var opt = function (v, label, title) {
      return '<option value="' + esc(v) + '"' + (v === chapFilter ? " selected" : "")
        + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(label) + "</option>";
    };
    var html = opt("", i18n.t("app.chapter.all"));
    /* 名字里的斜杠只影响**显示分组**（章节不嵌套，见 chapter.nest.note）。
       分组用的是 core 已经拆好的 parts[0]，这边不认识那个分隔符。

       同一组的几章要收在**一个** optgroup 里，所以这里按组聚一次而不是顺着清单
       边走边开：章节的先后是「谁先被开启」（core 按最早那一步的 id 排），
       主实验/画图 和 主实验/检索 完全可能中间隔着别的章节，顺着走就会开出两个
       同名的分组框——同一个名字在下拉框里出现两次，读的人只会以为自己看花了眼。
       组之间仍按**首次出现**的先后，组内仍按 core 给的顺序：两者都还是那句
       「和步骤列表同向」。 */
    var groups = [], byGroup = Object.create(null);
    CH.list.forEach(function (c) {
      var g = c.group;
      if (!byGroup[g]) { byGroup[g] = []; groups.push(g); }
      byGroup[g].push(c);
    });
    var one = function (c) {
      return opt(c.name, i18n.t("chapter.entry.bare", { chapter: c.name })
        + "  " + i18n.t("count.steps", { n: c.n }), c.note || i18n.t("chapter.desc.missing"));
    };
    groups.forEach(function (g) {
      if (!g) { byGroup[g].forEach(function (c) { html += one(c); }); return; }
      html += '<optgroup label="' + esc(g) + '" title="'
        + esc(i18n.t("chapter.group.title")) + '">';
      byGroup[g].forEach(function (c) { html += one(c); });
      html += "</optgroup>";
    });
    // 「只看还没分章的那些」也得是一个选项：一个项目分了章之后，剩下没归位的
    // 那几步正是最容易被忘掉的，而它们在 core 那边本来就自成一组（名字是空串）。
    if (CH.unassigned.length) {
      html += opt(CHAP_NONE, i18n.t("chapter.none")
        + "  " + i18n.t("count.steps", { n: CH.unassigned.length }),
        i18n.t("chapter.none.title"));
    }
    box.innerHTML = html;
  }

  /* 这一步在不在当前筛选范围内。没筛就人人都在。 */
  function inChapFilter(id) {
    if (!chapFilter) return true;
    var name = CH.of[id] || "";
    return chapFilter === CHAP_NONE ? !name : name === chapFilter;
  }
  /* 这一步归哪一章（继承来的那个名字），以及它该用第几号色相。
     **归属只问 CH.of**（core 的 resolve_chapters），绝不看这一步自己写没写。 */
  function chapOf(id) {
    var name = CH.of[id] || "";
    return name ? (CH.byName[name] || null) : null;
  }
  /* 卡片/行上那一道章节色条要带的属性。没有章节时返回空串——现存项目连一个
     多余的属性都不该多出来。 */
  function chapAttrs(s) {
    var c = CH.declared && chapOf(s.id);
    if (!c) return "";
    var own = s.chapter && s.chapter.declared;
    return ' data-chap="' + esc(c.name) + '" data-chi="' + c.hue + '"'
      + (own ? ' data-chdecl="1"' : "");
  }

  /* -------------------------------------------------------------- 图视图 */

  /* 一个分叉点底下**不属于候选组**的那些兄弟的中心 x。括弧要在它们头上留口子，
     否则圈住的卡片数和牌子上数出来的对不上。
     放在模块作用域而不是 renderDiagram 里面：renderForkLabels 是另一个函数，
     它也要画同一道括弧——藏在 renderDiagram 内部的话它根本看不见，
     一调用就抛，而抛点之后正好是画卡片那一句（症状是括弧在、卡片全没了）。 */
  function outsiderXs(nodes, g, nw) {
    var opt = {};
    (g.options || []).forEach(function (id) { opt[id] = 1; });
    var xs = [];
    F.steps.forEach(function (s) {
      if ((s.parent || "") !== (g.at || "") || opt[s.id]) return;
      var n = nodes && nodes[s.id];
      if (n) xs.push(n.x + nw / 2);
    });
    return xs;
  }

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

    /* 章节的底色带**排在最前面**，于是它落在所有边和卡片底下——章节是一层背景，
       不是图上的一样东西。按层切段（U.chapterBands）保证一块带子覆盖的只可能是
       本章的卡片：一个章节完全可以横跨好几棵树，随手圈一个大方框就会把别人家的
       节点圈进来，而人相信的正是自己看见的那一圈。 */
    CH.list.forEach(function (c) {
      U.chapterBands(T.nodes, c.steps, { nw: NW, nh: NH }).forEach(function (b) {
        out.push('<rect class="chband ch-' + c.hue + '" data-chap="' + esc(c.name) + '"'
          + ' x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h
          + '" rx="9"/>');
      });
    });

    // 跨章节的那些 parent 边（消融那条线是从主实验某一步分出去的）。
    var crossParent = Object.create(null);
    CH.crossings.forEach(function (x) { if (x.kind === "parent") crossParent[x.to] = x; });

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
      // b-alt 只换颜色。线型仍然是 s-<status>，不透明度仍然只归祖先链/搜索命中：
      // 这条边说的是「A/B 只能选一条」，说的不是「它做完了没有」。
      var rel = s.branch === U.BRANCH_ALT ? " b-alt" : "";
      out.push('<path class="dedge s-' + s.status + rel + '" data-id="' + esc(s.id) + '" d="' + d + '"/>');
      out.push('<path class="darrow s-' + s.status + rel + '" data-id="' + esc(s.id) + '" d="M'
        + (cx - 4.5) + " " + tip + "L" + (cx + 4.5) + " " + tip + "L" + cx + " " + (tip + 7) + 'Z"/>');
      /* 这条边跨章节：一对小斜杠钉在子节点正上方那一竖上。**新通道**——
         线型还是 status、颜色还是三种关系、不透明度还是「和选中有没有关系」。
         它说的是「消融那条线是从主实验的这一步分出去的」，值得画出来。 */
      var xp = crossParent[s.id];
      if (xp) {
        out.push('<path class="xchap" data-id="' + esc(s.id) + '" data-from="' + esc(p.id)
          + '" data-to="' + esc(s.id) + '" d="'
          + U.crossTick({ x: cx, y: midY }, { x: cx, y: tip }) + '"><title>'
          + esc(i18n.t("chapter.cross.parent", { chapter: xp.from_chapter || i18n.t("chapter.none") })
                + " — " + i18n.t("chapter.cross.parent.title")) + "</title></path>");
      }
    });

  /* 互斥候选的括弧。颜色一眼扫到，括弧说得准——灰度打印出来，「这几条是一组」
       仍然看得见。它是纯叠加层：core 保证候选不会被重排、不额外占轨道，
       所以直接按现成坐标画，一个节点都不用挪。 */
    (F.branch_groups || []).forEach(function (g) {
      var bk = U.forkBracket(T.nodes, g, { nw: NW, skip: outsiderXs(T.nodes, g, NW) });
      if (!bk) return;
      out.push('<path class="dfork" data-fork="' + esc(g.at) + '" d="' + bk.d + '"/>');
    });

    /* 汇回。曲线（不是正交折线）＋ 箭头，形状本身就说明它不属于树。
       全部都画、不藏起来：汇回本来就少（两端必须分属两条支线才算），
       藏起来的代价是「这条支线看着像死胡同，其实它绕回来了」——而那正是
       这个功能要修的那件事。选中之后不相关的照常淡出（不透明度那个通道的
       原意就是「和选中有没有关系」）。 */
    (F.merges || []).forEach(function (m) {
      var cv = U.rejoinCurve(T.nodes[m.from], T.nodes[m.to], { nw: NW, nh: NH });
      if (!cv) return;
      var tag = ' data-mfrom="' + esc(m.from) + '" data-mto="' + esc(m.to) + '"';
      out.push('<path class="drejoin"' + tag + ' d="' + cv.d + '"/>');
      out.push('<path class="drejoinhead"' + tag + ' d="' + cv.arrow + '"/>');
    });
    svg.innerHTML = out.join("");

    renderForkLabels(T, NW);

    holder.innerHTML = F.steps.map(function (s) {
      var n = T.nodes[s.id];
      if (!n) return "";
      var pics = (s.files || []).filter(function (f) { return IMG.test(f.path); }).length;
      var other = (s.files || []).length - pics;
      var marks = (pics ? '<span class="cmk" title="' + esc(i18n.t("count.images", { n: pics })) + '">🖼' + (pics > 1 ? pics : "") + "</span>" : "")
        + (other ? '<span class="cmk" title="' + esc(i18n.t("count.files", { n: other })) + '">📎' + (other > 1 ? other : "") + "</span>" : "")
        + nodeMarks(s);
      return '<div class="card s-' + s.status + '" data-id="' + esc(s.id) + '" tabindex="0"'
        + chapAttrs(s)
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

  /* 括弧旁边那句标注，以及「在决定什么」。
   *
   * 用 HTML 叠层而不是 SVG <text>：标注要截断、要 tooltip、要跟着深浅主题走，
   * 这三件事在 HTML 里是三行 CSS，在 SVG 里都得自己算。层放在卡片**下面**，
   * 于是它永远抢不走卡片的点击。
   *
   * 决策问题（父节点的 `decision:`）**默认不铺在图上**：它是自由文本，长度不定，
   * 一直显示的话，一棵有三个岔路口的树上就有三段长句压在别的支线上，缩小之后
   * 糊成一片。它改成两档：
   *   一直在  —— 「3 选 1」/「已定：012b」这种两三个字的状态（这是真正的信息量），
   *              整句问题挂在 title 里，悬停就有；
   *   选中时  —— 选中这个岔路口或它的任何一个候选，整句问题就在括弧下方展开。
   * 「点开这个岔路口」正是「告诉我这里在决定什么」这个动作本身，所以这一档
   * 不需要另外教，而不选中的时候图形状一个像素都不变。
   */
  /* 章节在图上的名牌，落在这个章节的**每一个入口**上（core 给的 roots：
     parent 不在同一章的那些成员）。一个章节可以横跨好几棵树，所以名牌可能有好几块——
     那正是「章节是一组步骤，不是一棵子树」这句话在图上的样子。
     写着「章节从这里开始」的那种（declared）和继承来的入口画成两回事：前者是
     改一个字就能把整条线搬走的那个锚点。 */
  function chapterLabels(T, NW) {
    if (!CH.declared) return "";
    var out = "";
    CH.list.forEach(function (c) {
      c.roots.forEach(function (id) {
        var n = T.nodes[id];
        if (!n) return;
        var s = IDX[id] || {};
        var declared = !!(s.chapter && s.chapter.declared);
        var tip = (c.note ? c.name + " — " + c.note : c.name) + " · "
          + i18n.t(declared ? "chapter.declared.title" : "chapter.badge.title");
        out += '<div class="chaplabel ch-' + c.hue + (declared ? " decl" : "")
          + '" data-chap="' + esc(c.name) + '" data-chapat="' + esc(id) + '"'
          + ' style="left:' + (n.x - 4) + "px;top:" + (n.y - 17) + 'px" title="' + esc(tip) + '">'
          + (declared ? '<span class="chdot">◆</span>' : '<span class="chdot">◇</span>')
          + esc(c.name) + "</div>";
      });
    });
    return out;
  }

  function renderForkLabels(T, NW) {
    var box = $("#dmarks");
    if (!box) return;
    box.innerHTML = chapterLabels(T, NW) + (F.branch_groups || []).map(function (g) {
      var bk = U.forkBracket(T.nodes, g, { nw: NW, skip: outsiderXs(T.nodes, g, NW) });
      var lab = U.forkLabel(g);
      if (!bk || !lab) return "";
      var q = g.at
        ? (g.decision || i18n.t("decision.question.missing"))
        : i18n.t("decision.roots.note");
      var tip = i18n.t(lab.title, lab.vars) + " — " + q;
      var cls = "forklabel f-" + lab.state + (bk.side ? " side" : "");
      /* 两档定位，锚的边不一样：
         · 普通：translate(-50%,-100%) 把盒子的**下沿**钉在 top 上，所以给的是
           「括弧再往上 3px」——标注整条落在父卡片和括弧之间那道缝里。
         · side（根之间那一组，括弧上面没有节点、也没有多少画布）：盒子的**上沿**
           钉在 top 上，而且**夹到 0 以上**。以前这里也走居中（translate(0,-50%)），
           于是标注一半落在 y<0，被滚动容器裁掉——`side` 这个分支本来就是为了
           「别被裁掉」而存在的，居中让它只完成了一半：换到右边躲开了卡片，
           却仍然被画布上沿切掉一半的字。 */
      var style = bk.side
        ? "left:" + (bk.x2 + 8) + "px;top:" + Math.max(bk.y - 8, 0) + "px"
        : "left:" + bk.cx + "px;top:" + (bk.y - 3) + "px";
      return '<div class="' + cls + '" data-fork="' + esc(g.at) + '" style="' + style + '"'
        + ' title="' + esc(tip) + '">'
        + '<span class="fstate">' + esc(i18n.t(lab.key, lab.vars)) + "</span>"
        + '<span class="fq">' + esc(q) + "</span>"
        + "</div>";
    }).join("");
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

    /* 跨章节的边在这张图上更要紧：消融吃着主实验的产物，那条 `input:` 正是
       「消融是对着主结果测的」这句话本身。两端的章节名进 <title>，记号本身
       和树上那一处是同一对小斜杠（同一个新通道，别处一个都没借）。 */
    var crossAt = Object.create(null);
    CH.crossings.forEach(function (x) { crossAt[x.from + ">" + x.to] = x; });

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
      var xc = crossAt[e.from + ">" + e.to];
      if (xc) {
        // 三次贝塞尔在 t=0.5 上正好是两端的中点（两个控制点各自只沿 y 偏移），
        // 所以记号钉在中点上就是钉在曲线上，不用去解曲线。画在边之后 = 压在边上。
        out.push('<path class="xchap"' + tag + ' d="'
          + U.crossTick({ x: x1, y: y1 }, { x: x2, y: y2 }) + '"><title>'
          + esc(i18n.t(xc.kind === "input" ? "chapter.cross.input" : "chapter.cross.parent",
                       { chapter: xc.from_chapter || i18n.t("chapter.none") })
                + " — " + i18n.t(xc.kind === "input"
                                 ? "chapter.cross.input.title" : "chapter.cross.parent.title"))
          + "</title></path>");
      }
    });
    svg.innerHTML = out.join("");

    holder.innerHTML = (F.steps || []).map(function (s) {
      var n = FLOW.nodes[s.id];
      if (!n) return "";
      var off = (s.inputs || []).filter(function (i) { return i.step !== s.parent; }).length;
      return '<div class="fcard s-' + s.status + '" data-id="' + esc(s.id) + '" tabindex="0"'
        + chapAttrs(s)
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

  /* 汇回的曲线要从轨道右边绕过去，所以有汇回时给轨道图多留一条空档。
     轨道之间的竖线是满的，曲线从中间穿会和它们缠在一起。没有汇回就不留——
     一条永远空着的白边只会把行文本推走。 */
  var MERGE_GUTTER = 18;

  function renderRails() {
    var RH = F.row_h || 28, LW = F.lane_w || 14;
    var svg = $("#rails");
    var merges = F.merges || [];
    var w = RAIL_PAD * 2 + Math.max(0, (F.lane_count || 1) - 1) * LW
      + (merges.length ? MERGE_GUTTER : 0);
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
      var rel = s.branch === U.BRANCH_ALT ? " b-alt" : "";
      out.push('<path class="edge s-' + s.status + rel + '" data-id="' + esc(s.id) + '" d="' + d + '"/>');
    });

    /* 轨道图有它自己的语汇，别硬套图视图那一套。
       互斥候选：在分叉那一行的节点**正下方**画一道横跨这几条候选轨道的括弧。
       定高行 + 十几像素宽的轨道装不下弧形括弧，所以压成一道带两端小钩的短线——
       它仍然是那个「把这一组括起来」的记号，而且不占任何行高。
       根之间那一组（at 为空）没有可以挂的那一行，只在顶栏的未决横幅和详情面板里说。 */
    (F.branch_groups || []).forEach(function (g) {
      var p = g.at && IDX[g.at];
      if (!p) return;
      var ls = (g.options || []).filter(function (id) { return IDX[id]; })
        .map(function (id) { return IDX[id].lane; });
      if (!ls.length) return;
      // 两头各伸出 4px：不伸的话这道 ⊓ 正好和轨道之间那段拐弯一样宽，
      // 在十几像素的沟里根本分不出「这是一道括弧」还是「这是又一条边」。
      var a = x(Math.min.apply(null, ls)) - 4, b = x(Math.max.apply(null, ls)) + 4;
      var by = y(p.row) + 7;                     // 紧贴分叉那个节点的下沿
      out.push('<path class="railfork" data-fork="' + esc(g.at) + '" d="M'
        + a + " " + (by + 3) + "V" + by + "H" + b + "V" + (by + 3) + '"/>');
    });

    /* 汇回：从右边的空档绕过去的一条曲线加箭头。git graph 里横过来并进主线的
       那条线就是这个位置、这个形状，所以读起来不需要另外教。多条汇回各让开
       几像素，免得叠成一条看不出有几处。 */
    var outX = w - 4;
    merges.forEach(function (m, i) {
      var a = IDX[m.from], b = IDX[m.to];
      if (!a || !b) return;
      var cv = U.railRejoin({ x: x(a.lane), y: y(a.row) }, { x: x(b.lane), y: y(b.row) },
                            outX - (i % 3) * 4);
      var tag = ' data-mfrom="' + esc(m.from) + '" data-mto="' + esc(m.to) + '"';
      out.push('<path class="railjoin"' + tag + ' d="' + cv.d + '"/>');
      out.push('<path class="railjoinhead"' + tag + ' d="' + cv.arrow + '"/>');
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
      // 章节在这一档是行首那一道色条（data-chap）。行高一个像素都不许变——
      // 轨道 SVG 和行文本只靠固定行高对齐，所以它走 inset box-shadow，不占位置。
      return '<div class="row s-' + s.status + '" data-id="' + esc(s.id) + '"' + chapAttrs(s) + ">"
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
    /* 三种关系在**行/卡片自己身上**也各留一个字形——图上那些颜色和形状滚出
       视野之后（列表里搜到一行、图上只看得见一张卡），这一个字符就是仅剩的线索。
       Y 和 ~> 是记号不是词，两种语言里都不翻。字形是照着**字体真的有、而且
       在 9px 下还分得开**挑的：⑂（U+2482）要回退到别的字体，糊成一个看不出
       形状的小疙瘩；⤳ 回退之后只剩一条波浪线；⇝ 画出来和旁边那个 ⇢（数据来源
       计数）几乎一样，而两者的颜色又都偏冷——两个通道同时失效。~> 是 ASCII，
       哪儿都印得出来，也不可能和箭头认混。 */
    if (s.fork) {
      var lab = U.forkLabel(s.fork);
      out += '<span class="cmk fork" title="'
        + esc(i18n.t(lab.title, lab.vars) + " — "
              + (s.decision || i18n.t("decision.question.missing")))
        + '">Y' + (s.fork.options || []).length + "</span>";
    }
    if (s.branch === U.BRANCH_ALT) {
      out += '<span class="cmk alt" title="' + esc(i18n.t("branch.badge.title")) + '">Y</span>';
    }
    var joins = (s.merge_in || []).length + (s.merge_out || []).length;
    if (joins) {
      out += '<span class="cmk join" title="' + esc(i18n.t("rejoin.badge.title")) + '">~&gt;'
        + (joins > 1 ? joins : "") + "</span>";
    }
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
    /* 开发路径上的回指：这一步也在定稿流程里，而且是第几步。
       仍然是**字形**这个第四通道——线型归 status、不透明度归祖先链/搜索命中、
       颜色归三种关系，一个都没借。方括号里的数字和那张图上、Methods 草稿里的
       编号是同一个，于是两条路径互相指得回去。
       `s.pipeline` 只在项目声明了成果时才存在（现存项目完全无感）。 */
    var pl = s.pipeline;
    if (pl && pl.member) {
      var num = (pl.index === null || pl.index === undefined) ? "" : String(pl.index + 1);
      out += '<span class="cmk pipe" title="'
        + esc(i18n.t(pl.result ? "pipeline.result.badge.title" : "pipeline.badge.title"))
        + '">[' + esc(num) + "]</span>";
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
    /* 这四条 core 一直在发，而这张表里一直没有——于是英文界面的警告栏
       原样漏出整句中文。它们都不带变量（`where` 已经指明了是哪个文件），
       所以只要一个 key 就够，不需要从句子里抠值。 */
    missing_why: { key: "lint.missing.why" },
    missing_what: { key: "lint.missing.what" },
    missing_conclusion: { key: "lint.missing.conclusion" },
    figure_without_caption: { key: "lint.figure.nocaption" },
    table_without_explanation: { key: "lint.table.nodesc" },
    code_without_explanation: { key: "lint.pre.nodesc" },
    dangling_input: { key: "input.warn.missing", pick: /(\S+)\s*$/, as: "id" },
    self_input: { key: "input.warn.self", pick: /[（(]([^）)]+)[）)]/, as: "id" },
    input_cycle: { key: "input.warn.cycle", pick: /:\s*([^—]+)/, as: "chain" },
    /* 分叉的三条。它们**没有 pick**：core 把该说的值结构化地放进了 w.vars，
       而从中文句子里抠数字是那条老的、脆得离谱的退路，新代码不该再长出来。
       lone_alternative 一个变量都不用（文案里没有占位符）。 */
    lone_alternative: { key: "lint.alt.lone" },
    fork_without_decision: { key: "lint.fork.noquestion", take: ["n"] },
    undecided_fork: { key: "lint.fork.open", take: ["n"] },
    decision_without_candidates: { key: "lint.fork.nocandidates", take: ["id"] },
    /* `branch:` 拼错时 core 报的降级提醒。没有这一条的时候它会退回 esc(w.message)，
       也就是在英文界面上原样漏出一整句中文——而这条恰恰是给写错字的人看的。 */
    bad_branch: { key: "lint.branch.unknown", take: ["branch"] },
    /* 定稿流程那三条判断。它们走的是 forest.pipeline.diagnostics，**不进警告栏**
       （见 pipeChecks），但换文案用的是同一张表——同一个 code 在两处说两句不同的
       话，比漏翻还糟。带变量的一律 take（core 已经把 ids 拼成了一个字符串、
       n 是数字），绝不从中文句子里抠。 */
    pipeline_no_result: { key: "pipeline.warn.noresult" },
    pipeline_dead_step: { key: "pipeline.warn.dead", take: ["n", "ids"] },
    pipeline_weak_step: { key: "pipeline.warn.weak", take: ["n", "ids"] },
    /* core 发的是**七**条，上面只有三条时剩下四条在英文界面上原样漏出中文——
       退回 esc(w.message) 那条兜底是对的（绝不吞警告），但漏出来的恰好是四条
       「记录里两句话打架」，也就是最需要人读懂的那几条。 */
    pipeline_excluded_consumed: { key: "pipeline.warn.excluded.consumed", take: ["n", "id", "ids"] },
    pipeline_excluded_result: { key: "pipeline.warn.excluded.result", take: ["id"] },
    pipeline_cycle: { key: "pipeline.warn.cycle", take: ["n", "ids"] },
    dangling_result: { key: "pipeline.warn.dangling", take: ["id"] },
    /* `pipeline:` 写错取值时 core 的降级提醒。走的是 forest.warnings（不是
       pipeline.diagnostics），和 bad_branch 并排——它只在有人真写了这一行时才出现，
       而那个人正是最需要读懂这句话的人。 */
    bad_pipeline: { key: "lint.pipeline.unknown", take: ["pipeline"] },
    /* ⑨ 章节那四条。变量名**逐字**照抄 core 放进 w.vars 的那几个（warnText 不改名），
       所以这里写的是 name / names / ids / id / note，不是 chapter——页面自己拼的
       文案里 {chapter} 才是章节名，这四条是唯一的例外，i18n 那侧有一条断言钉着。
       前三条走 forest.chapters.diagnostics（和 pipeline 的诊断同一档，**不进顶栏
       警告栏**：现存项目必须完全无感），bad_chapter 走 forest.warnings，和
       bad_branch / bad_pipeline 并排——它只在有人真写坏了那一行时才出现。 */
    chapter_note_conflict: { key: "lint.chapter.desc.conflict", take: ["name", "ids", "id"] },
    chapter_no_result: { key: "lint.chapter.noresult", take: ["name"] },
    chapter_near_duplicate: { key: "lint.chapter.nearduplicate", take: ["names"] },
    bad_chapter: { key: "lint.chapter.unnamed", take: ["note"] },
  };
  /* w.vars 里那个值，取成字符串。**不许只认 string**：core 的
     validate_branches 发的是 {"n": len(options)}，那是个**数字**，
     用 typeof === "string" 判会把它判掉，于是整条退回去抠中文正则，
     抠不出来就在英文界面上原样漏出一整句中文。 */
  function warnVar(w, k) {
    var v = w && w.vars ? w.vars[k] : undefined;
    if (v === undefined || v === null || v === "") return "";
    return String(v);
  }
  /* 选出这一条警告该用哪条文案、带哪些变量。认不出来返回 null（调用方退回
     服务端原句）。抽出来是因为同一条判断现在有两个出口：屏幕上那条走 tHtml
     （文案里有 `行内代码` 和 **粗体**），Methods 草稿和独立页面走 t()。
     两处各写一遍的话，同一条诊断在页面上和在导出里会说成两句不同的话。 */
  function warnPick(w) {
    var m = WARN_MAP[w.code];
    if (!m || !i18n.has(m.key)) return null;
    if (m.take) {
      var got = {}, ok = true;
      m.take.forEach(function (k) {
        var v = warnVar(w, k);
        if (!v) ok = false; else got[k] = v;
      });
      return ok ? { key: m.key, vars: got } : null;
    }
    if (!m.pick) return { key: m.key, vars: {} };
    var vars = {};
    var direct = warnVar(w, m.as);
    if (direct) {
      vars[m.as] = direct;
    } else {
      var hit = m.pick.exec(w.message || "");
      if (!hit) return null;
      vars[m.as] = hit[1].trim();
    }
    return { key: m.key, vars: vars };
  }
  /* 出的是 HTML：这些文案里有 `行内代码` 和 **粗体**，走 t() 的话反引号会原样
     显示给用户看。认不出来时退回服务端那句中文，那一条要 esc——它是别处来的字节。 */
  function warnText(w) {
    var got = warnPick(w);
    if (!got) return esc(w.message);
    return i18n.tHtml(got.key, got.vars);
  }
  function renderWarnings() {
    var bar = $("#warnbar"), ws = F.warnings || [];
    bar.hidden = !ws.length;
    if (!ws.length) return;
    var by = { error: [], warn: [], hint: [], todo: [] };
    ws.forEach(function (w) { by[U.warnLevel(w)].push(w); });
    bar.hidden = !(by.error.length || by.warn.length || by.hint.length);
    if (bar.hidden) return;                 // 只剩待办时整条栏都不出现
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

  /* 「我还有几个岔路口没做决定」——这是整个候选组功能真正的收益，也是隔了一个月
     回到一个项目时最该先看的一句话。它只画在单步详情里就等于没有：那要求人先
     猜到该点哪一步。所以和「有几处位置已经不在了」一样，一条横幅摆在顶上、点得进去。

     它不是警告：同时开几条线是研究的常态。所以样式走的是 #missbar 那一档
     （一条陈述），不是 #warnbar 那一档。 */
  function renderForks() {
    var bar = $("#forkbar");
    if (!bar) return;
    var open = (F.branch_groups || []).filter(function (g) { return g.state === "open"; });
    bar.hidden = !open.length;
    if (!open.length) return;
    bar.innerHTML = '<b title="' + esc(i18n.t("decision.open.summary.title")) + '">Y '
      + esc(i18n.t("decision.open.summary", { n: open.length })) + "</b>"
      + open.map(function (g) {
          // 根之间那一组没有分叉点可以跳，跳到它的第一个候选——那是唯一
          // 能让人看到这一组的地方（详情面板里那一块）。
          var id = g.at || (g.options || [])[0] || "";
          return id ? " " + stepLink(id) : "";
        }).join(" ·");
  }

  /* ======================================================== ⑧ 定稿流程视图
   *
   * 这一档画的是**另一批步骤**：trace_core 从 project.md 的 `result:` 出发、沿
   * `input:` 反向做闭包算出来的那条链（够不着输入时退回 parent，dead 剔掉）。
   * 成员清单一个字都不存，所以移动一步、补一条 input、把某支标 dead，它自己就变。
   *
   * 它要**看起来像一张方法图，不像一棵树**：没有岔路口、没有 dead、没有「我当时
   * 为什么试这个」。每一步显示的东西全部按「别人照着做」来挑——做了什么、命令、
   * 代码位置、产物和校验和、这一步的等级。「为什么试这个」是开发路径的事，
   * 那条路径一步都没删，一个按钮就跳得回去。
   *
   * 屏幕上那张图和导出的 SVG 是**同一个出口的同一份字节**（exportURL("figure")）：
   * 让屏幕一张、发出去另一张，等于对着一张图讨论、拿另一张去投稿。
   */
  var PIPE = { declared: false, order: [], steps: [], results: [], diagnostics: [], chapters: [] };

  /* 章节名在界面上的显示名。未分章那一组的名字是空串（core 的约定），它照样
     是一组真的成果，所以给它一个说得出口的名字，而不是让它变成一行空白。 */
  function chapName(name) { return name || i18n.t("chapter.none"); }

  /* 顶栏筛选器此刻聚焦的是哪个章节。null = 没筛（看整个项目那一条流程）。 */
  function chapFocusName() {
    if (!chapFilter) return null;
    return chapFilter === CHAP_NONE ? "" : chapFilter;
  }
  function pipeGroup(name) {
    var got = null;
    (PIPE.chapters || []).forEach(function (g) { if (g.name === name) got = g; });
    return got;
  }

  /* 服务端认不认 `?chapter=`。
   *
   * 按章节导出的那三样字节**只有一份实现**，在 Python 那一侧；这一页只是指过去。
   * 于是有一件事必须先问清楚：这台服务端到底会不会按章节编。不问就挂上按钮的话，
   * 一台还没升级的服务端会**忽略这个查询参数**、老老实实回整个项目的那一份，
   * 而文件名上写着「消融」——「屏幕上讨论的是一张图、投出去的是另一张」正是
   * 这一整档设计要挡的那件事，何况这一次连文件名都在撒谎。
   *
   * 判据是服务端**自己说**它编的是哪一章（payload 里回一个 `chapter`）。
   * 认不出来就一个按章节的按钮都不画，整项目那三样照旧——少一个按钮是遗憾，
   * 一份名不副实的 Methods 草稿是事故。
   *
   * 那个 `chapter` 是**一个对象**（trace_mcp.pipeline_payload 给的
   * `{name, label, external, known, no_result}`），不是一个字符串——拿整个对象
   * 去和名字比永远不相等，于是探针永远说「这台服务端不认」，而症状不是报错，
   * 是按章节导出整块**静默消失**。所以那一步比较剥成了 U.chapterEcho，
   * 由 tests/test_seams_chapter.py 拿真服务端的响应喂给它当场量。
   */
  var CH_PIPE_OK = 0;              // 0 还没问 / 2 正在问 / 1 认 / -1 不认
  function probeChapterPipeline(name) {
    if (MODE !== "server" || CH_PIPE_OK) return;
    CH_PIPE_OK = 2;
    fetch(BASE + "/api/p/" + encodeURIComponent(PROJECT) + "/pipeline?chapter="
          + encodeURIComponent(name))
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (d) {
        CH_PIPE_OK = (U.chapterEcho(d) === name) ? 1 : -1;
        if (mode === "pipeline") renderPipeline();
      });
  }
  function chapExportable(name) {
    if (CH_PIPE_OK !== 1 || name === null || name === undefined) return false;
    /* 未分章那一组在查询串上只有记号可用，而一个**真叫 `-` 的章节**会赢过它
       （真名优先，Python 侧逐字同一条规矩）。真撞上了就不摆这个按钮：宁可少一份
       导出，也不要一份文件名写着「未分章」、内容却是别人那一章的 Methods。 */
    if (!name) return !CH.byName[CHAP_SENT];
    return true;
  }

  /* 「这一步凭什么在流程里」。四选一的枚举由 core 给（result / include / input /
     parent），这里只负责说成话；认不出来的取值就不说——编一句比不说更糟。 */
  function pipeWhy(w) {
    var key = "pipeline.why." + ((w && w.kind) || "");
    return i18n.has(key) ? i18n.t(key, { id: (w && w.id) || "" }) : "";
  }

  /* 把 forest.pipeline 整理成这一屏和三样导出**共用**的那一份模型。
     标题跟界面语言走、「做了什么」按内容语言从正文里取——两件事都在这里做完，
     纯函数层（U.pipelineModel）一个字的界面文案都不持有。 */
  function buildPipeline() {
    PIPE = U.pipelineModel(F, {
      title: stepTitle,
      what: function (s) { return U.sectionOf(stepBody(s), templates(), "what"); },
      whyText: pipeWhy,
    });
    return PIPE;
  }

  function pipeLevelLine() {
    var m = PIPE;
    return m.level ? m.level + " " + levelName(m.level) : "";
  }
  function pipeMeans() {
    var key = "pipeline.level.means." + PIPE.level;
    return PIPE.level && i18n.has(key) ? i18n.t(key) : "";
  }
  function pipeWeakestLine() {
    var w = PIPE.weakest && IDX[PIPE.weakest];
    return w ? i18n.t("pipeline.level.weakest", { link: w.id, title: stepTitle(w) }) : "";
  }

  /* 空态。绝大多数项目打开定稿流程看到的就是这一屏，所以它按「是什么 / 为什么
     值得声明 / 怎么声明 / 去声明」四段写，而且第三段里就是那一行 `result:` 的
     真实写法——按钮这条路要服务端配合（见接口说明），而在项目笔记里手写一行
     永远有效，那一条不能只藏在文档里。 */
  function pipeEmpty() {
    return '<div class="pdoc pempty">'
      + '<h2 class="viewtitle">' + esc(i18n.t("pipeline.empty.title")) + "</h2>"
      + '<p class="viewlead">' + i18n.tHtml("pipeline.empty.what") + "</p>"
      + '<p class="viewlead">' + i18n.tHtml("pipeline.empty.why") + "</p>"
      + '<p class="viewlead">'
      + i18n.tHtml("pipeline.empty.how", { act: i18n.t("pipeline.empty.act") }) + "</p>"
      + (canWrite()
          ? '<p class="pacts"><button class="primary" data-act="result-mark" title="'
            + esc(i18n.t("pipeline.result.mark.title")) + '">'
            + esc(i18n.t("pipeline.empty.act")) + "</button></p>"
          : "")
      + '<p class="viewlead quiet">' + i18n.tHtml("pipeline.pair.note") + "</p>"
      + "</div>";
  }

  function pipeResults(group) {
    var m = PIPE;
    // 按章节看时只列这一章声明的那几个成果：这条流程正是从它们反推出来的。
    if (group) {
      var want = Object.create(null);
      (group.results || []).forEach(function (id) { want[id] = 1; });
      m = { results: m.results.filter(function (r) { return want[r.step]; }) };
    }
    if (!m.results.length) return "";
    return '<div class="sec presults"><h3>'
      + esc(i18n.t("pipeline.result.head", { n: m.results.length })) + "</h3>"
      + '<p class="dropnote deplead">' + i18n.tHtml("pipeline.result.lead") + "</p>"
      + '<ul class="deplist">' + m.results.map(function (r) {
          var link = '<a href="#' + esc(r.step) + '" data-pgoto="' + esc(r.step) + '">'
            + esc(r.step) + "</a>";
          return "<li>" + (r.note
            ? i18n.tHtml("pipeline.result.entry", { link: { html: link }, what: r.note })
            : i18n.tHtml("pipeline.result.entry.bare", { link: { html: link } })) + "</li>";
        }).join("") + "</ul>"
      + (m.results.length > 1
          ? '<p class="dropnote">' + esc(i18n.t("pipeline.result.multi")) + "</p>" : "")
      + "</div>";
  }

  /* 整条流程的可溯源等级 —— 一个数回答「别人能不能照着做出来」，并**指名**
     是哪一步拖的后腿（可点，跳到这一屏里那一步的卡片）。 */
  function pipeLevel() {
    var m = PIPE;
    if (!m.level) return "";
    var w = m.weakest && IDX[m.weakest];
    var weak = w
      ? '<p class="lvcap">' + i18n.tHtml("pipeline.level.weakest", {
          link: { html: '<a href="#' + esc(w.id) + '" data-pgoto="' + esc(w.id) + '">'
            + esc(w.id) + "</a>" },
          title: stepTitle(w),
        }) + "</p>"
      : "";
    return '<div class="sec plevel"><h3>' + esc(i18n.t("pipeline.level.head")) + "</h3>"
      + '<div class="lvrow">' + lvChip(m.level) + "</div>"
      + '<p class="lvmean">' + esc(pipeMeans()) + "</p>"
      + weak
      + '<p class="dropnote">' + esc(i18n.t("pipeline.level.note")) + "</p></div>";
  }

  /* 三条判断，写论文前最想知道的那几件事。它们**只在这里**说，绝不进顶栏的
     警告栏（forest.pipeline.diagnostics 和 forest.warnings 是两份东西）：
     定稿流程的事让每个项目每次打开都多一条顶栏提示，人很快连真警告一起不看了。 */
  function pipeChecks() {
    var ds = PIPE.diagnostics || [];
    if (!ds.length) return "";
    return '<div class="sec pchecks"><h3>' + esc(i18n.t("pipeline.check.head")) + "</h3>"
      + ds.map(function (w) {
          var lv = w.level === "info" ? "info" : U.warnLevel(w);
          return '<div class="wrow w-' + esc(lv) + '">'
            + (lv === "info" ? "· " : "⚠ ")
            + "<b>" + esc(w.where || w.code) + "</b> — " + warnText(w) + "</div>";
        }).join("") + "</div>";
  }

  /* 三样导出的地址。**它们是同一份生成器的三个出口**，这一页一笔都不画。
     服务模式走那三条只读路由；静态导出没有服务端，走 `build` 写在同目录的三个
     文件（figure 那一份还被灌进了页面本身，见 PIPE_SVG）。
     两条路拿到的都是 trace_mcp 里 pipeline_svg / pipeline_methods / pipeline_page
     的输出——**屏幕上看到的就是发出去的那批字节**。 */
  var EXPORT_FILE = { figure: "pipeline.svg", methods: "pipeline.md", page: "pipeline.html" };
  var EXPORT_ROUTE = { figure: "figure.svg", methods: "methods.md", page: "page.html" };
  /* 这一屏此刻在导**哪一章**。只有这一份判据：屏幕上那张图、三个下载按钮、
     那句 toast 全从它来，谁也不可能漏传一个参数而各指一处——「屏幕上讨论的
     和投出去的不是同一份」这条毛病，最省事的堵法就是不给它第二个入口。
     空串 = 整个项目那一条（没筛，或者服务端不认按章节编）。
     **null** = 整个项目那一条（没筛，或者服务端不认按章节编）。
     **空串** = 未分章那一组：它的名字本来就是空的，可它是一组真的成果——多数项目
     的主线从头到尾没起过名字，最该单独导一份 Methods 的恰恰是它。空串在查询串上
     和「没给」长得一模一样，所以那一组用记号 CHAP_SENT 指（= trace_mcp.CHAPTER_NONE）。 */
  function exportChapter() {
    var ch = chapFocusName();
    return ch !== null && chapExportable(ch) ? ch : null;
  }
  /* 放进 `?chapter=` 的那个值。名字**原样**送出去（不折叠大小写、不做近似匹配：
     服务端认不出会回 404，替人猜一次，导出的是哪一章就取决于猜法，
     而其中一份会进论文）。空串 = 不加这个参数。 */
  function exportChapterParam() {
    var ch = exportChapter();
    return ch === null ? "" : (ch || CHAP_SENT);
  }
  function exportURL(kind) {
    if (!EXPORT_FILE[kind]) return "";
    if (MODE === "static") return EXPORT_FILE[kind];
    var u = BASE + "/api/p/" + encodeURIComponent(PROJECT) + "/pipeline/" + EXPORT_ROUTE[kind];
    var ch = exportChapterParam();
    return ch ? u + "?chapter=" + encodeURIComponent(ch) : u;
  }
  /* 按章节导出的文件名。章节名**不是路径安全的**（`主实验/数据准备` 合法、`CON`
     合法），所以名字是派生出来的（U.chapterSlug + 顺序号去重），绝不拿原名拼。
     去重按的是章节在清单里的顺序：大小写是故意不折叠的（core 靠它逮笔误），
     于是两个不同章节完全可能 slug 成同一个词。 */
  var CHAP_STEM = Object.create(null);
  function chapStems() {
    CHAP_STEM = Object.create(null);
    var names = CH.list.map(function (c) { return c.name; });
    /* 未分章那一组也要有一个自己的词干。它不在 core 的章节清单里（它不是一个
       章节），但它常常就是主实验——导它那一份的时候文件名不能是空的，
       更不能和整项目那份撞名。 */
    if (CH.unassigned.length) names.push("");
    U.chapterFileStems(names).forEach(function (st, i) { CHAP_STEM[names[i]] = st; });
  }
  function exportName(kind) {
    var stem = (PROJECT || "trace") + "-", ch = exportChapter();
    return stem + (ch === null ? "" : (CHAP_STEM[ch] || U.chapterSlug(ch)) + "-") + EXPORT_FILE[kind];
  }

  /* 那张图的字节。静态导出里 `build` 已经灌进页面（file:// 下 fetch 一个相对
     路径会被当成跨源，取不到），服务模式下按需取一次并按 forest 版本缓存。
     取不到时**留空**、不画一张自己拼的图：一张来路不明的图比没有图糟得多。 */
  var PIPE_SVG = "";
  var PIPE_SVG_AT = -1;      // 手上这份图对应第几次 apply()（见 FOREST_SEQ）
  var PIPE_SVG_WANT = -1;    // 正在取的是第几次的（同一版不重复发请求）
  (function () {
    var el = document.getElementById("pipeline-svg");
    var raw = el && el.textContent.trim();
    if (raw) { try { PIPE_SVG = JSON.parse(raw) || ""; } catch (e) { PIPE_SVG = ""; } }
  })();

  /* 手上这份图的身份：**这一次编译**（FOREST_SEQ）× **这一章**。两样有一样变了，
     手上那份就过期了。编译轮次由调用方传进来而不是在这里读全局，是为了让
     「我问的是哪一版」写在问的那一行上——一张过期的方法图会被当成现在的方法图。 */
  function figureKey(seq) { return seq + "|" + exportChapterParam(); }
  function pipeFigure() {
    // 屏幕上就是那张要进论文的图：纸上的墨、黑白可读、不跟主题走。
    // 换一套「屏幕好看版」的代价是人对着一张图讨论、发出去另一张。
    /* 手上这份图必须**正好**是这一屏要的那一份（这一版记录 × 这一章）。
       按章节看和看整项目是两张图，谁也不许暂时顶替谁——一张顶替上去的图会被
       当成这一章的方法图，那比空着糟得多。静态导出里灌进来的那份永远是整项目那张。 */
    var ok = MODE === "static" ? exportChapter() === null : (PIPE_SVG_AT === figureKey(FOREST_SEQ));
    return '<div class="pfig" id="pfig">' + (ok ? PIPE_SVG : "") + "</div>";
  }

  /* 服务模式下把那张图取回来填进去。**不重画**：如果这次没取到，上一次那张
     （版本可能已经旧了）也不留在屏幕上——一张过期的图会被当成现在的方法图。 */
  function fetchFigure() {
    if (MODE === "static" || !PIPE.declared) return;
    var want = figureKey(FOREST_SEQ);
    // 已经取到这一版、或这一版正在取的路上，就不再发第二次：renderPipeline
    // 一次 apply 里会被调两遍（apply 一遍、项目名到了再一遍）。
    if (PIPE_SVG_AT === want || PIPE_SVG_WANT === want) return;
    PIPE_SVG_WANT = want;
    fetch(exportURL("figure")).then(function (r) {
      return r.ok ? r.text() : "";
    }).catch(function () { return ""; }).then(function (svg) {
      if (PIPE_SVG_WANT !== want) return;      // 这期间又变了，别用旧的盖掉新的
      PIPE_SVG = svg || "";
      PIPE_SVG_AT = svg ? want : -1;
      var box = $("#pfig");
      if (box) box.innerHTML = PIPE_SVG;
    });
  }

  function pipeSteps(items, group) {
    var ext = Object.create(null);
    ((group && group.external) || []).forEach(function (id) { ext[id] = 1; });
    return (items || []).map(function (it) {
      var s = it.step || {};
      var badges = "";
      if (it.result) {
        badges += '<span class="pipechip result" title="'
          + esc(i18n.t("pipeline.result.badge.title")) + '">'
          + esc(i18n.t("pipeline.result.badge")) + "</span>";
      }
      /* **借来的那几步**：这条流程里不属于本章的成员。消融吃着主实验的 023，
         那 023 和它的上游当然要出现在消融的 Methods 里（一个输入不在流程里的
         成员，写进 Methods 就是一句断了的话），但它们是借来的，得标出来——
         那正是「消融是对着主结果测的」这句话在这一屏上的样子。 */
      if (ext[it.id]) {
        badges += '<span class="chapchip" title="' + esc(i18n.t("chapter.badge.title")) + '">'
          + esc(i18n.t("chapter.of.head", { chapter: chapName(CH.of[it.id] || "") }))
          + "</span>";
      }
      if ((s.pipeline || {}).rule === "include") {
        badges += '<span class="pipechip" title="' + esc(i18n.t("pipeline.include.badge.title"))
          + '">' + esc(i18n.t("pipeline.include.badge")) + "</span>";
      }
      var what = it.what
        ? '<div class="prose">' + window.md.render(it.what, { resolve: resolverFor(s) }) + "</div>"
        : '<p class="dropnote pmiss">' + esc(i18n.t("trace.missing.what")) + "</p>";
      return '<section class="pstep" data-pstep="' + esc(it.id) + '">'
        + '<div class="phead"><span class="pnum mono">' + it.n + "</span>"
        + '<span class="pid mono">' + esc(it.id) + "</span>"
        + '<h3 class="ptitle">' + esc(it.title || i18n.t("common.untitled")) + "</h3>"
        + lvChip(it.level, "mini") + badges
        + '<span class="sp"></span>'
        // 这就是「两条路径都留着」的全部意义：这一步当时有几个候选、为什么选了它，
        // 全在开发路径那边，而这里一个按钮就过去。
        + '<button data-devgoto="' + esc(it.id) + '" title="'
        + esc(i18n.t("pipeline.jump.dev.title")) + '">'
        + esc(i18n.t("pipeline.jump.dev")) + "</button></div>"
        + (it.whyText ? '<p class="pwhy quiet">' + esc(it.whyText) + "</p>" : "")
        + what
        + renderCode(s) + renderPaths(s)
        + "</section>";
    }).join("");
  }

  /* 三个导出。**它们不在这里生成**——三样都指到那唯一一份实现的产物上
     （服务模式是三条只读路由，静态导出是 build 写在同目录的三个文件）。
     都是纯函数的产物：同一份记录导两次逐字节一致，所以正确的做法是要用的时候
     重新生成，不是把导出存进仓库。文案里的 export.lead 把这句话说给用户听。

     做成 <a download> 而不是「取回来再 Blob 一下」：file:// 下 fetch 一个相对
     路径会被当成跨源，静态导出里那三个按钮会一按什么都不发生。 */
  function pipeExport() {
    // null = 整项目那一份。空串是**未分章那一组**（一组真的成果），所以这里
    // 判的是 !== null 而不是真值——`!""` 会把它错当成「没在导某一章」。
    var ch = exportChapter(), show = ch === null ? "" : chapName(ch);
    var one = function (kind, key) {
      return '<div class="pexp"><a class="btn" data-export="' + kind + '"'
        + (show ? ' data-expchap="' + esc(show) + '"' : "") + ' href="'
        + esc(exportURL(kind)) + '" download="' + esc(exportName(kind)) + '" title="'
        + esc(i18n.t(key + ".note")) + '">' + esc(i18n.t(key)) + "</a>"
        + '<span class="dropnote">' + esc(i18n.t(key + ".note")) + "</span></div>";
    };
    /* 按章节导的时候多说两句：为什么它是另一份派生而不是「整项目那份过滤几行」，
       以及文件名长什么样——导消融那一份永远不会盖掉主实验的 Methods 草稿，
       靠的就是文件名里那一段。 */
    var head = ch !== null
      ? '<h3>' + esc(i18n.t("export.chapter.head")) + "</h3>"
        + '<p class="dropnote deplead">' + i18n.tHtml("export.chapter.note") + "</p>"
        + '<p class="dropnote"><b>' + esc(i18n.t("export.chapter.one", { chapter: show }))
        + "</b> · " + '<span title="' + esc(i18n.t("export.chapter.file.title")) + '">'
        + esc(i18n.t("export.chapter.file", { file: exportName("methods") })) + "</span></p>"
      : '<h3>' + esc(i18n.t("export.head")) + "</h3>"
        + '<p class="dropnote deplead">' + i18n.tHtml("export.lead") + "</p>";
    return '<div class="sec pexport">' + head
      + '<div class="pexps">' + one("figure", "export.figure")
      + one("methods", "export.methods") + one("page", "export.page") + "</div>"
      + '<p class="dropnote">' + i18n.tHtml("export.draft.note") + "</p></div>";
  }

  /* 这一章还没有任何 `result:`。它不是错误（一个章节完全可以只是探索性的），
     所以这一屏和整项目的空态同一个路子：说清楚为什么这里是空的，并且把那一行
     `result:` 的真实写法印出来——按钮那条路要服务端配合，手写那一行永远有效。 */
  function pipeChapterEmpty(name) {
    return '<div class="pdoc pempty">'
      + '<h2 class="viewtitle">' + esc(i18n.t("chapter.pipeline.head", { chapter: chapName(name) })) + "</h2>"
      + '<p class="viewlead">' + i18n.tHtml("chapter.pipeline.none", { chapter: chapName(name) }) + "</p>"
      + '<p class="viewlead quiet">' + i18n.tHtml("chapter.pipeline.note") + "</p>"
      + (canWrite()
          ? '<p class="pacts"><button class="primary" data-act="result-mark" title="'
            + esc(i18n.t("pipeline.result.mark.title")) + '">'
            + esc(i18n.t("pipeline.empty.act")) + "</button></p>"
          : "")
      + "</div>";
  }

  /* 只看这一章时的等级。**只按本章的成员算**，不把别的章节的祖先算进来——
     它回答的是读者对某一块真正会问的那个问题：消融这部分别人能不能重做。 */
  function pipeChapterLevel(g) {
    if (!g || !g.level) return "";
    var w = g.weakest && IDX[g.weakest];
    var weak = w
      ? '<p class="lvcap">' + i18n.tHtml("chapter.level.weakest", {
          link: { html: '<a href="#' + esc(w.id) + '" data-pgoto="' + esc(w.id) + '">'
            + esc(w.id) + "</a>" },
          title: stepTitle(w),
        }) + "</p>"
      : "";
    return '<div class="sec plevel"><h3>'
      + esc(i18n.t("chapter.level.head", { chapter: chapName(g.name) })) + "</h3>"
      + '<div class="lvrow">' + lvChip(g.level) + "</div>"
      + weak
      + '<p class="dropnote">' + esc(i18n.t("chapter.level.note")) + "</p></div>";
  }

  /* 没筛章节时，整项目那条流程后面附一份「各章各自那条」的清单。
     每一章有自己的成果、自己的等级、自己的一段 Methods——论文里本来就是两段。 */
  function pipeChapterList() {
    if (!CH.declared || !PIPE.chapters.length) return "";
    return '<div class="sec pchaps"><h3>'
      + esc(i18n.t("chapter.head", { n: CH.list.length })) + "</h3>"
      + '<p class="dropnote deplead">' + i18n.tHtml("chapter.pipeline.note") + "</p>"
      + '<ul class="deplist">' + CH.list.map(function (c) {
          var g = pipeGroup(c.name);
          /* 没有成果的那一章不写成一条错误：一个章节完全可以只是探索性的。
             整句「怎么给它一条自己的流程」放在 tooltip 里，行上只报事实。 */
          var body = g
            ? lvChip(g.level, "mini") + '<span class="deptitle">'
              + esc(i18n.t("count.steps", { n: g.order.length })) + " · "
              + esc(g.results.join(" · ")) + "</span>"
            : '<span class="deptitle quiet" title="'
              + esc(i18n.t("chapter.pipeline.none", { chapter: chapName(c.name) })) + '">'
              + esc(i18n.t("chapter.steps", { steps: i18n.t("count.steps", { n: c.n }),
                    done: c.status.done, wip: c.status.wip, dead: c.status.dead })) + "</span>";
          return '<li class="chrow ch-' + c.hue + '">' + chapGoBtn(c.name) + " " + body + "</li>";
        }).join("") + "</ul></div>";
  }

  function renderPipeline() {
    var box = $("#pwrap");
    if (!box) return;
    buildPipeline();
    if (!PIPE.declared) { box.innerHTML = pipeEmpty(); return; }
    /* 顶栏那个章节筛选器在这一档换的是**编哪一条流程**：主实验一条、消融一条，
       各有自己的成果、自己的等级、自己那份导出。它不是「把整项目那份过滤掉几行」
       ——每一章是从它自己的 `result:` 反推出来的另一份派生（core 已经把那一张
       DAG 切好了，这里一步闭包都不重算）。 */
    var focus = chapFocusName();
    if (focus !== null && CH.declared) probeChapterPipeline(focus || CH.list[0].name);
    var group = focus === null ? null : pipeGroup(focus);
    if (focus !== null && !group) { box.innerHTML = pipeChapterEmpty(focus); return; }
    var items = group ? U.pipelineChapterSteps(PIPE, group) : PIPE.steps;
    box.innerHTML = '<div class="pdoc">'
      + '<h2 class="viewtitle">' + esc(group
          ? i18n.t("chapter.pipeline.head", { chapter: chapName(group.name) })
          : i18n.t("pipeline.head", { n: PIPE.order.length })) + "</h2>"
      + '<p class="viewlead">' + i18n.tHtml(group ? "chapter.pipeline.note" : "pipeline.lead") + "</p>"
      + '<p class="viewlead quiet">' + i18n.tHtml("pipeline.pair.note") + "</p>"
      + (group ? pipeChapterLevel(group) : pipeLevel())
      + pipeResults(group) + pipeChecks() + (group ? "" : pipeChapterList())
      // 按章节看的时候，只有服务端真会按章节编时才摆那张图（见 probeChapterPipeline）。
      + (group && exportChapter() === null ? "" : pipeFigure())
      + '<p class="dropnote">' + esc(i18n.t("pipeline.order.note")) + "</p>"
      + pipeSteps(items, group)
      + pipeExport()
      + '<p class="dropnote pfoot">' + i18n.tHtml("pipeline.derived.note") + "</p>"
      + "</div>";
    enhanceProse(box);
    fetchFigure();
  }

  /* 按下三个导出之一。真正的下载由 <a download> 自己完成（这个处理器**不**
     preventDefault），这里只负责说一声——那句话里带着「逐字节确定」这件事，
     人知道了才会去重新生成，而不是把导出存进仓库当第二份真相。 */
  function doExport(kind, chapter) {
    var name = { figure: "export.figure", methods: "export.methods", page: "export.page" }[kind];
    // 认不出的取值什么都不说：一次拼错的按钮名不该冒充成一次成功的导出。
    if (!name || !PIPE.declared) return;
    /* 按章节导的时候说清是哪一章：一份写着「消融」的文件如果其实是整个项目，
       收到的人不会发现，而它可能就是投出去的那一份。
       章节名取自**被点的那个链接自己**（data-expchap），不是重新算一遍页面状态：
       说出来的必须是刚刚下载的那一份的身份。 */
    toast(chapter
      ? i18n.t("toast.export.chapter.ready", { chapter: chapter, name: i18n.t(name) })
      : i18n.t("toast.export.ready", { name: i18n.t(name) }));
  }

  /* 在这一屏里跳到某一步的卡片。和跳回开发路径是两个动作：这个不换模式。 */
  function pipeScrollTo(id) {
    var el = document.querySelector('#pwrap [data-pstep="' + id + '"]');
    if (el) el.scrollIntoView({ block: "start", inline: "nearest" });
  }

  function setMode(next, keep) {
    if (MODES.indexOf(next) < 0 || next === mode) return;
    mode = next;
    localStorage.setItem("trace.mode", mode);
    applyView();
    renderSelection();
    if (!keep) scrollToSelected();
  }

  function applyView() {
    // 窄屏切到图视图时先自动适应宽度，否则第一眼是画布左上角那一小块
    if (view === "graph" && window.innerWidth < NARROW && F.tree && F.tree.w > $("#scroller").clientWidth) {
      fitZoom();
    }
    var dev = mode === "dev";
    $("#dwrap").hidden = !dev || view !== "graph";
    $("#track").hidden = !dev || view !== "list";
    $("#fwrap").hidden = !dev || view !== "flow";
    $("#pwrap").hidden = dev;
    $("#empty").hidden = !dev || F.steps.length > 0;
    $("#zoombar").hidden = !dev || view !== "graph";
    // 图例跟着视图换：数据流那三条说的是边的意思，和 status 那三条不是一回事，
    // 同时摆出来只会让人以为「实线 = 树边」。
    $("#treelegend").hidden = view === "flow";
    $("#flowlegend").hidden = view !== "flow";
    document.querySelectorAll("#viewtoggle button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-view") === view);
    });
    document.querySelectorAll("#modetoggle button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-mode") === mode);
    });
    /* 定稿流程是一份从头读到尾的**文档**，不是一棵可以戳的树，所以它占满整块
       工作区、并且不带右边那个详情面板——详情面板里装的正是这一档不该显示的东西
       （为什么试这个、几个候选、移动史）。想看那些就按「回开发路径上看它」。
       第二级的画法切换和图例也一起收起来：这一档只有一种画法。 */
    document.body.classList.toggle("pipeline-mode", !dev);
    $("#viewtoggle").hidden = !dev;
    $("#legend").hidden = !dev || !F.steps.length;
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
      /* 章节筛选**只 dim，绝不 hide**——和搜索那条是同一条理由：隐藏会打乱轨道
         对齐、改变图的形状，而形状本身是信息（「消融是从主实验哪一步分出去的」
         这句话，正是靠周围那些被筛掉的节点还在原位才看得见）。
         用的也是同一个通道：不透明度承载的一直是「和你此刻的关注有没有关系」，
         祖先链、搜索、章节筛选说的是同一件事的三种问法。 */
      el.classList.toggle("offchap", !inChapFilter(id));
      el.classList.toggle("sel", id === sel);
    });
    // 底色带、名牌、跨章记号跟着一起淡：筛到消融时，主实验那块满亮的底色
    // 读起来就是「这块也是你要看的」。
    document.querySelectorAll("#dedges .chband, #dmarks .chaplabel").forEach(function (el) {
      el.classList.toggle("offchap", !!chapFilter && el.getAttribute("data-chap") !== chapFilter);
    });
    document.querySelectorAll(".xchap").forEach(function (el) {
      var a = el.getAttribute("data-from") || el.getAttribute("data-id");
      var b = el.getAttribute("data-to") || el.getAttribute("data-id");
      el.classList.toggle("offchap", !(inChapFilter(a) || inChapFilter(b)));
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

    /* 括弧跟着它的分叉点淡出（和树边同一条判据）。

       根之间那一组的 at 是空串，没有分叉点可以查——但**不能因此就永远不淡**：
       不透明度这个通道只表示「和你选中的那条链有没有关系」，一道满亮的括弧
       压在两张已经灰掉的卡片上，读者读到的就是「这一组和你有关」，而它在另一棵树上。
       根组的等价判据是「它的候选里有没有一个在你这条链上」——语义完全一致，
       而且天然成立：选中哪棵树，哪棵树的根就在链上。 */
    var groupAt = {};
    (F.branch_groups || []).forEach(function (g) { groupAt[g.at || ""] = g; });
    document.querySelectorAll("[data-fork]").forEach(function (el) {
      var at = el.getAttribute("data-fork");
      var near = at ? !!chain[at]
                    : ((groupAt[""] || {}).options || []).some(function (o) { return !!chain[o]; });
      el.classList.toggle("faded", !!sel && !near);
    });
    /* 汇回边**不能**套「两端都在祖先链上」那条判据：它按定义两端分属两条支线，
       套上去就是一选中任何节点，所有汇回集体消失。判据在 U.rejoinRelated 里。 */
    document.querySelectorAll("[data-mfrom]").forEach(function (el) {
      var m = { from: el.getAttribute("data-mfrom"), to: el.getAttribute("data-mto") };
      var near = U.rejoinRelated(m, sel, chain);
      el.classList.toggle("faded", !near);
      el.classList.toggle("sel", sel === m.from || sel === m.to);
    });
    /* 「在决定什么」只在选中这个岔路口、或选中它的任何一个候选时展开：
       它是自由文本，一直铺在图上会压住别的支线，缩小之后糊成一片。 */
    document.querySelectorAll("#dmarks .forklabel").forEach(function (el) {
      var at = el.getAttribute("data-fork");
      var g = null;
      (F.branch_groups || []).forEach(function (x) { if (x.at === at) g = x; });
      var on = !!sel && (sel === at || (g && (g.options || []).indexOf(sel) >= 0));
      el.classList.toggle("on", !!on);
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
          /* 哪一行 input 是汇回，问的是 step.merge_in——**不要**去 i 上找 rel：
             inputs 是文件里那几行的逐字镜像，故意没有派生字段（别人被移动一次，
             同一行的归类就会翻转，混在一起读的人会以为磁盘变了）。 */
          if ((s.merge_in || []).indexOf(i.step) >= 0) {
            line += ' <span class="joinbadge" title="' + esc(i18n.t("rejoin.kind.title")) + '">'
              + esc(i18n.t("rejoin.kind")) + "</span>";
          }
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

  /* ⑦ 决策分叉：这一步底下那个岔路口，以及这一步自己是不是某个岔路口的候选。
   *
   * 两件事都是**派生**的，磁盘上只有每个候选自己那句 `branch: alternative`：
   *   「这一组有谁」  扫兄弟现算（core 的 branch_groups）
   *   「选中了哪个」  其余候选标 dead 就是选择本身，没有第二个字段
   * 所以这一块里永远不会出现一个「标记赢家」按钮——那个按钮就是双真相源。
   * 文案（decision.lead / decision.of.lead）把这句话直接说给用户听，因为不说
   * 的话人会一直在界面上找它。
   */
  function candidateRows(g) {
    return '<ul class="deplist candlist">' + (g.options || []).map(function (id) {
      var c = IDX[id];
      var link = stepLink(id);
      var what = c ? (c.branch_note || stepTitle(c) || "") : "";
      var line = what
        ? i18n.tHtml("decision.candidate.entry", { link: { html: link }, what: what })
        : i18n.tHtml("decision.candidate.entry.bare", { link: { html: link } });
      var dead = c && c.status === "dead";
      return '<li class="cand' + (dead ? " out" : "") + '">' + line
        + '<span class="candst">'
        + esc(i18n.t(dead ? "decision.candidate.dead" : "decision.candidate.live"))
        + "</span></li>";
    }).join("") + "</ul>";
  }

  function renderFork(s) {
    var out = "";
    var g = s.fork;
    if (g) {
      var lab = U.forkLabel(g);
      var head = g.at
        ? '<h3>' + esc(i18n.t("decision.head", { n: (g.options || []).length })) + "</h3>"
        : '<h3>' + esc(i18n.t("decision.roots.head", { n: (g.options || []).length })) + "</h3>";
      // 根之间那一组没有节点能承载 `decision:`，别让人到处找那个写问题的地方
      var q = g.at
        ? '<p class="decq"><b>' + esc(i18n.t("decision.question.label")) + "</b>"
          + '<span title="' + esc(i18n.t("decision.question.title")) + '">'
          + (s.decision ? esc(s.decision)
                        : '<i class="quiet">' + esc(i18n.t("decision.question.missing")) + "</i>")
          + "</span></p>"
        : '<p class="decq quiet">' + esc(i18n.t("decision.roots.note")) + "</p>";
      var note = g.state === "open"
        ? '<p class="dropnote">' + esc(i18n.t("decision.open.note", { n: (g.live || []).length })) + "</p>"
        : "";
      out += '<div class="sec decbox">' + head
        + '<p class="dropnote deplead">' + i18n.tHtml("decision.lead") + "</p>"
        + q
        + '<p class="decstate f-' + lab.state + '" title="' + esc(i18n.t(lab.title, lab.vars)) + '">'
        + esc(i18n.t(lab.key, lab.vars)) + "</p>"
        + candidateRows(g) + note + "</div>";
    } else if (s.decision) {
      /* 写了「在决定什么」，但底下一个候选都还没标。不显示的话，人刚写完那一行
         就在界面上找不到它了，只会以为没保存成功——而这一行是这套东西里唯一
         推导不出来、只能人写的字，最不该悄悄消失。 */
      out += '<div class="sec decbox"><p class="decq"><b>'
        + esc(i18n.t("decision.question.label")) + "</b>"
        + '<span title="' + esc(i18n.t("decision.question.title")) + '">' + esc(s.decision) + "</span></p>"
        /* 光把这一行摆出来还不够：人看到自己写的问题**在**页面上，却看不到任何
           候选，最容易得出的结论是「界面坏了」。所以紧跟一句说清它现在的状态
           ——记下来了，但还不成其为岔路口，以及下一步该做什么。 */
        + '<p class="dropnote">' + i18n.tHtml("decision.question.orphan") + "</p>"
        + "</div>";
    }
    if (s.branch === U.BRANCH_ALT) {
      var mine = U.groupOf(F.branch_groups, s);
      var others = mine ? (mine.options || []).length - 1 : 0;
      var lead = s.parent
        ? '<h3>' + esc(i18n.t("decision.of.head", { parent: s.parent })) + "</h3>"
          + '<p class="dropnote deplead">' + i18n.tHtml("decision.of.lead", { parent: s.parent }) + "</p>"
        : '<h3>' + esc(i18n.t("decision.roots.head", { n: mine ? (mine.options || []).length : 1 })) + "</h3>"
          + '<p class="dropnote deplead">' + esc(i18n.t("decision.roots.note")) + "</p>";
      var sibs = others > 0 && mine
        ? '<h4 class="rhead">' + esc(i18n.t("decision.siblings.head", { n: others })) + "</h4>"
          + candidateRows({ options: (mine.options || []).filter(function (id) { return id !== s.id; }) })
        : '<p class="dropnote">' + esc(i18n.t("decision.siblings.lone")) + "</p>";
      var pq = s.parent && IDX[s.parent] && IDX[s.parent].decision
        ? '<p class="decq"><b>' + esc(i18n.t("decision.question.label")) + "</b>"
          + esc(IDX[s.parent].decision) + "</p>"
        : "";
      out += '<div class="sec decbox altbox">' + lead + pq + sibs + "</div>";
    }
    return out;
  }

  /* 汇回。它**不是**第三种边，它就是 `input:`——只是以前只在数据流图和详情面板
     的依赖清单里出现过，树上看不见，于是一条绕回来的支线读着像死胡同。
     两个方向都要有：本步的产物汇回到了谁那儿，以及谁的产物汇进了本步。
     「两条路是在哪儿分开的」（core 算的 LCA）一并说出来——不说的话，
     「它绕回来了」只是一条曲线，没有具体的形状。 */
  function renderRejoin(s) {
    var ins = s.merge_in || [], outs = s.merge_out || [];
    if (!ins.length && !outs.length) return "";
    var at = function (from, to) {
      var got = "";
      (F.merges || []).forEach(function (m) { if (m.from === from && m.to === to) got = m.at; });
      return got ? '<span class="joinat">' + esc(i18n.t("rejoin.at", { id: got })) + "</span>" : "";
    };
    var rows = function (ids, other) {
      return '<ul class="deplist">' + ids.map(function (id) {
        var link = stepLink(id), what = titleOf(id);
        var line = what
          ? i18n.tHtml("rejoin.entry", { link: { html: link }, what: what })
          : i18n.tHtml("rejoin.entry.bare", { link: { html: link } });
        return "<li>" + line + other(id) + "</li>";
      }).join("") + "</ul>";
    };
    return '<div class="sec joinbox"><h3>' + esc(i18n.t("rejoin.kind")) + "</h3>"
      + '<p class="dropnote deplead">' + i18n.tHtml("rejoin.lead") + "</p>"
      + (outs.length
          ? '<h4 class="rhead">' + esc(i18n.t("rejoin.out.head", { n: outs.length })) + "</h4>"
            + rows(outs, function (id) { return at(s.id, id); })
          : "")
      + (ins.length
          ? '<h4 class="rhead">' + esc(i18n.t("rejoin.in.head", { n: ins.length })) + "</h4>"
            + rows(ins, function (id) { return at(id, s.id); })
          : "")
      + "</div>";
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

  /* ⑧ 这一步和定稿流程的关系 —— 开发路径这一侧的那半个入口。
   *
   * 整块只在项目**真的声明了成果**时才出现（`s.pipeline` 是 compile_forest 在
   * 有 `result:` 时才加的键）。没声明的项目一个字都不多出来。
   *
   * 三件事：它在不在流程里、凭什么在（core 给的四选一枚举）、以及跳到流程里去看。
   * 「这一步当时有 3 个候选」留在这一侧（上面那几块），「该怎么做」在那一侧——
   * 两条路径各说各的那一半，谁也不复述谁。
   */
  function renderPipelineOf(s) {
    var pl = s.pipeline;
    if (!pl) return "";
    if (!pl.member && !pl.rule) return "";
    var body = "";
    if (pl.member) {
      var w = (F.pipeline && F.pipeline.why && F.pipeline.why[s.id]) || null;
      var why = pipeWhy(w);
      body += '<p class="decq"><b>' + esc(i18n.t("pipeline.badge")) + "</b>"
        + '<span title="' + esc(i18n.t("pipeline.badge.title")) + '">'
        + esc(pl.index === null || pl.index === undefined ? "" : "[" + (pl.index + 1) + "] ")
        + esc(why) + "</span></p>"
        + '<p class="pacts"><button data-pipego="' + esc(s.id) + '" title="'
        + esc(i18n.t("pipeline.jump.final.title")) + '">'
        + esc(i18n.t("pipeline.jump.final")) + "</button></p>";
    }
    // 这一步自己写的那行 `pipeline:`。说明是必写的（写入侧拦着），所以它一定有话说。
    if (pl.rule) {
      body += '<p class="dropnote">'
        + esc(i18n.t("pipeline." + pl.rule + ".badge.title"))
        + (pl.note ? " — " + esc(pl.note) : "") + "</p>";
    }
    return '<div class="sec pipebox"><h3>' + esc(i18n.t("pipeline.name")) + "</h3>"
      + '<p class="dropnote deplead">' + i18n.tHtml("pipeline.lead") + "</p>" + body + "</div>";
  }

  /* ⑨ 这一步属于哪一章 —— 详情面板里那一块。
   *
   * 整块只在项目里**真有人写过 `chapter:`** 时才出现（`forest.chapters` 是 core 在
   * 那时候才加的键）。没有章节的项目一个字都不多出来。
   *
   * 四件事，缺一件这块就没用：
   *   1) 它属于哪一章（继承出来的那个名字，不是它自己写没写）；
   *   2) 这个归属是**继承来的还是自己声明的**，以及声明在哪一步——人要知道
   *      「改哪一步才能改整条线」，而那个答案只有一个；
   *   3) 这个章节是什么（说明归章节不归步骤）、有多大、能被追到哪一步；
   *   4) 同章还有哪些步、以及**跨出这一章的那几条边**（消融吃着主实验的产物）。
   */
  /* 一条跨章节的边写成一行。**方向由边自己定，不由「我是谁」定**：这句话说的
     永远是箭头那一端在做什么——「{to} 读的是 {from_chapter} 的产物」。
     于是本步在上游时，行首那个链接是对面那一步，而那句话仍然成立（说的是对面）；
     本步在下游时，链接是产物的来处。反过来按「本步」组织的话，同一条边在两头
     会被说成两句相反的话，而边只有一条。 */
  function crossRow(x, selfId) {
    var other = x.from === selfId ? x.to : x.from;
    var otherChap = x.from === selfId ? x.to_chapter : x.from_chapter;
    var link = stepLink(other);
    var kind = x.kind === "input" ? "chapter.cross.input" : "chapter.cross.parent";
    var line = x.note
      ? i18n.tHtml("chapter.cross.entry",
                   { link: { html: link }, chapter: chapName(otherChap), what: x.note })
      : i18n.tHtml("chapter.cross.entry.bare",
                   { link: { html: link }, chapter: chapName(otherChap) });
    return '<li><span class="xchip" title="' + esc(i18n.t(kind + ".title")) + '">'
      + esc(i18n.t(kind, { chapter: chapName(x.from_chapter) }))
      + "</span> " + line + '<span class="deptitle">' + esc(titleOf(other)) + "</span></li>";
  }

  function renderChapterOf(s) {
    if (!CH.declared) return "";
    var name = CH.of[s.id] || "";
    var c = name ? CH.byName[name] : null;
    var declared = !!(s.chapter && s.chapter.declared);
    var src = U.chapterSourceOf(IDX, s.id);

    var head = '<h3>' + esc(name ? i18n.t("chapter.of.head", { chapter: name })
                                 : i18n.t("chapter.none")) + "</h3>"
      + '<p class="dropnote deplead" ' + (name ? "" : 'title="' + esc(i18n.t("chapter.none.title")) + '"')
      + ">" + i18n.tHtml(name ? "chapter.of.lead" : "chapter.none.title") + "</p>";

    /* 「章节从这里开始」和「继承自 007」是两回事，而这个区别正是人最需要的那一条：
       前者是那个改一行就能把整条线搬走的锚点，后者只是跟着走的一步。 */
    var where = "";
    if (declared) {
      where = '<p class="chwhere decl" title="' + esc(i18n.t("chapter.declared.title")) + '">'
        + esc(i18n.t("chapter.declared")) + "</p>"
        + '<p class="dropnote">' + i18n.tHtml("chapter.inherit.note") + "</p>";
    } else if (src) {
      where = '<p class="chwhere" title="' + esc(i18n.t("chapter.inherited.title", { id: src })) + '">'
        + i18n.tHtml("chapter.inherited", { id: { html: stepLink(src) } }) + "</p>"
        + '<p class="dropnote">' + i18n.tHtml("chapter.leave.note") + "</p>";
    }

    var about = "";
    if (c) {
      about = '<p class="chdesc" title="' + esc(i18n.t("chapter.desc.title")) + '"><b>'
        + esc(i18n.t("chapter.desc.label")) + "</b>"
        + (c.note ? esc(c.note) : '<i class="quiet">' + esc(i18n.t("chapter.desc.missing")) + "</i>")
        + "</p>"
        + '<p class="chfacts">'
        + '<span class="chroots" title="' + esc(i18n.t("chapter.roots.title")) + '">'
        + esc(i18n.t("chapter.roots", { n: c.roots.length })) + "</span>"
        + (c.level ? lvChip(c.level, "mini") : "") + "</p>"
        + (c.weakest && IDX[c.weakest]
            ? '<p class="lvcap">' + i18n.tHtml("chapter.level.weakest", {
                link: { html: stepLink(c.weakest) }, title: stepTitle(IDX[c.weakest]) }) + "</p>"
            : "")
        + '<p class="dropnote">' + esc(i18n.t("chapter.level.note")) + "</p>";
    }

    // 同章还有哪些步。它们在图上被一块底色带圈着，这里给的是同一组的可点清单。
    var sibs = "";
    if (c && c.steps.length > 1) {
      /* 小标题就是这一章的规模与状态分布（chapter.steps）：一句话说完「这一章
         有多大、走通了多少」，底下紧跟着的就是那一批步骤本身。 */
      sibs = '<h4 class="rhead mono" title="' + esc(i18n.t("chapter.badge.title")) + '">'
        + esc(i18n.t("chapter.steps", { steps: i18n.t("count.steps", { n: c.n }),
              done: c.status.done, wip: c.status.wip, dead: c.status.dead })) + "</h4>"
        + '<div class="crumbs chsteps">' + c.steps.map(function (id) {
            return id === s.id ? "<b>" + esc(id) + "</b>" : stepLink(id);
          }).join(" ") + "</div>";
    }

    // 跨出这一章的边：这一步读的是别章的产物，或者这条线是从别章分出来的。
    var xs = CH.crossings.filter(function (x) { return x.from === s.id || x.to === s.id; });
    var cross = xs.length
      ? '<h4 class="rhead" title="' + esc(i18n.t("chapter.cross.note")) + '">'
        + esc(i18n.t("chapter.cross.head", { n: xs.length })) + "</h4>"
        + '<ul class="deplist xlist">' + xs.map(function (x) { return crossRow(x, s.id); }).join("") + "</ul>"
      : "";

    var acts = "";
    if (canWrite()) {
      acts = '<p class="pacts">'
        + '<button data-act="chapter" title="'
        + esc(i18n.t(declared ? "chapter.unset.act.title" : "chapter.set.act.title")) + '">'
        + esc(i18n.t(declared ? "chapter.unset.act" : "chapter.set.act")) + "</button>"
        + (c ? '<button data-act="chapter-note" title="'
              + esc(i18n.t("chapter.write.act.title")) + '">'
              + esc(i18n.t("chapter.write.act")) + "</button>" : "")
        + "</p>";
    }
    return '<div class="sec chapbox' + (c ? " ch-" + c.hue : "") + '">'
      + head + where + about + acts + sibs + cross + "</div>";
  }

  /* ⑨ 项目这一级的章节面板。没选步骤时详情面板就是项目主页，这一块和洞察并排。
   *
   * 三样东西只在这里说一次，绝不在别处再说一遍：各章是什么（含说明、步数、等级）、
   * **跨章节的那些边**、以及 core 那三条章节诊断。诊断**不进顶栏警告栏**——
   * 和定稿流程那三条同一条规矩：现存项目必须完全无感，而一条每次打开都在的提示
   * 只会让人从此不看提示栏。 */
  /* 「只看这一章」那个开关。名字本身就是开关（和顶栏那个筛选器同一份状态、
     同一份判据），所以清单里不再另摆一个写着同名的按钮。 */
  function chapGoBtn(value, label, title) {
    return '<button class="chapgo" data-chapgo="' + esc(value) + '" title="'
      + esc(title || i18n.t("app.chapter.title")) + '">'
      + esc(label === undefined ? value : label) + "</button>";
  }

  function renderChapters() {
    if (!CH.declared) return "";
    var rows = CH.list.map(function (c) {
      /* 章节名**本身**就是「只看这一章」那个开关（tooltip 说清点了会怎样）。
         名字旁边再摆一个写着同一个名字的按钮，同一行里就出现了两次同一个词，
         而人得先读完两遍才知道那是一回事。 */
      var line = c.note
        ? i18n.tHtml("chapter.entry", { chapter: { html: chapGoBtn(c.name) }, what: c.note })
        : i18n.tHtml("chapter.entry.bare", { chapter: { html: chapGoBtn(c.name) } });
      return '<li class="chrow ch-' + c.hue + '">'
        + line
        + '<span class="deptitle mono">'
        + esc(i18n.t("chapter.steps", { steps: i18n.t("count.steps", { n: c.n }),
              done: c.status.done, wip: c.status.wip, dead: c.status.dead })) + "</span>"
        + (c.level ? lvChip(c.level, "mini") : "")
        + '<span class="chroots" title="' + esc(i18n.t("chapter.roots.title")) + '">'
        + esc(i18n.t("chapter.roots", { n: c.roots.length })) + "</span>"
        + "</li>";
    }).join("");
    var none = CH.unassigned.length
      ? '<li class="chrow chnone">'
        + chapGoBtn(CHAP_NONE, i18n.t("chapter.none"), i18n.t("chapter.none.title"))
        + '<span class="deptitle mono">'
        + esc(i18n.t("count.steps", { n: CH.unassigned.length })) + "</span></li>"
      : "";
    var cross = CH.crossings.length
      ? '<h4 class="rhead">' + esc(i18n.t("chapter.cross.head", { n: CH.crossings.length })) + "</h4>"
        + '<p class="dropnote deplead">' + i18n.tHtml("chapter.cross.note") + "</p>"
        + '<ul class="deplist xlist">' + CH.crossings.map(function (x) {
            return '<li><span class="xchip" title="'
              + esc(i18n.t(x.kind === "input" ? "chapter.cross.input.title"
                                              : "chapter.cross.parent.title")) + '">'
              + esc(i18n.t(x.kind === "input" ? "chapter.cross.input" : "chapter.cross.parent",
                           { chapter: chapName(x.from_chapter) })) + "</span> "
              + (x.note
                  ? i18n.tHtml("chapter.cross.entry", { link: { html: stepLink(x.to) },
                      chapter: chapName(x.to_chapter), what: x.note })
                  : i18n.tHtml("chapter.cross.entry.bare", { link: { html: stepLink(x.to) },
                      chapter: chapName(x.to_chapter) }))
              + '<span class="deptitle">' + esc(titleOf(x.to)) + "</span></li>";
          }).join("") + "</ul>"
      : "";
    var checks = CH.diagnostics.length
      ? '<div class="chchecks">' + CH.diagnostics.map(function (w) {
          var lv = w.level === "info" ? "info" : U.warnLevel(w);
          return '<div class="wrow w-' + esc(lv) + '">' + (lv === "info" ? "· " : "⚠ ")
            + "<b>" + esc(w.where || w.code) + "</b> — " + warnText(w) + "</div>";
        }).join("") + "</div>"
      : "";
    return '<div class="insights chappanel"><h2 class="title">'
      + esc(i18n.t("chapter.head", { n: CH.list.length })) + "</h2>"
      + '<p class="dropnote deplead">' + i18n.tHtml("chapter.lead") + "</p>"
      + '<p class="dropnote">' + i18n.tHtml("chapter.vs.note") + "</p>"
      + '<ul class="deplist chlist">' + rows + none + "</ul>"
      + cross + checks
      // 两件**刻意不做**的事。不写在这儿的话，后来人会当成漏了去补：
      // id 不按章节重编号（[[007]] 要在整个项目里唯一）· 章节不嵌套（斜杠只分组）。
      + '<p class="dropnote quiet">' + i18n.tHtml("chapter.ids.note") + "</p>"
      + '<p class="dropnote quiet">' + i18n.tHtml("chapter.nest.note") + "</p>"
      + "</div>";
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
      // ⑨ 章节面板紧跟洞察：两者都是**项目这一级**的东西，而没选步骤时这一栏
      // 就是项目主页。没有章节的项目这里一个字都不多（renderChapters 返回空串）。
      + renderChapters()
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
    /* 「我是一个候选」和「我底下是一个岔路口」得在第一眼就看得见：它们决定了
       这一步该怎么读——一个候选的 dead 是「这条路没走通」，不是「这一步做砸了」。 */
    if (s.branch === U.BRANCH_ALT) {
      meta.push('<span class="altchip" title="' + esc(i18n.t("branch.badge.title")) + '">'
        + esc(i18n.t("branch.badge")) + "</span>");
    }
    if (s.fork) {
      var flab = U.forkLabel(s.fork);
      meta.push('<span class="forkchip f-' + flab.state + '" title="'
        + esc(i18n.t(flab.title, flab.vars)) + '">' + esc(i18n.t(flab.key, flab.vars)) + "</span>");
    }
    if ((s.merge_in || []).length || (s.merge_out || []).length) {
      meta.push('<span class="joinchip" title="' + esc(i18n.t("rejoin.badge.title")) + '">'
        + esc(i18n.t("rejoin.badge")) + "</span>");
    }
    /* 「它在定稿流程里」得在第一眼看得见：这一步的记录写成什么样，从此不只是
       自己的事——它会跟着成果被别人照着做一遍。 */
    var pl0 = s.pipeline;
    if (pl0 && pl0.result) {
      meta.push('<span class="pipechip result" title="'
        + esc(i18n.t("pipeline.result.badge.title")) + '">'
        + esc(i18n.t("pipeline.result.badge")) + "</span>");
    } else if (pl0 && pl0.member) {
      meta.push('<span class="pipechip" title="' + esc(i18n.t("pipeline.badge.title")) + '">'
        + esc(i18n.t("pipeline.badge")) + "</span>");
    }
    if (pl0 && pl0.rule === "include") {
      meta.push('<span class="pipechip" title="' + esc(i18n.t("pipeline.include.badge.title"))
        + '">' + esc(i18n.t("pipeline.include.badge")) + "</span>");
    }
    if (pl0 && pl0.rule === "exclude") {
      meta.push('<span class="pipechip out" title="' + esc(i18n.t("pipeline.exclude.badge.title"))
        + '">' + esc(i18n.t("pipeline.exclude.badge")) + "</span>");
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
        /* 标成候选 / 取消。落盘的只有这一步自己那一行 `branch:`——绝不去兄弟
           身上登记什么，也绝不写一个「选中了谁」的字段。 */
        + '<button data-act="branch" title="'
        + esc(i18n.t(s.branch === U.BRANCH_ALT ? "decision.unmark.act.title" : "decision.mark.act.title"))
        + '">' + esc(i18n.t(s.branch === U.BRANCH_ALT ? "decision.unmark.act" : "decision.mark.act"))
        + "</button>"
        /* 标成成果 / 撤回。这是整条定稿流程里**唯一**写下来的一件事，其余
           （谁在流程里、什么顺序、能追到多远）全是从它反推出来的，所以这里
           永远不会有一个「编辑流程成员」的按钮——那就是第二份真相。 */
        + '<button data-act="result" title="'
        + esc(i18n.t((s.pipeline && s.pipeline.result)
              ? "pipeline.result.unmark.title" : "pipeline.result.mark.title"))
        + '">' + esc(i18n.t((s.pipeline && s.pipeline.result)
              ? "pipeline.result.unmark" : "pipeline.result.mark")) + "</button>"
        // 「在决定什么」写在**分叉点**上，所以只有底下真有支的步骤才给这个入口
        + ((s.children || []).length || s.decision
            ? '<button data-act="decision" title="' + esc(i18n.t("decision.write.act.title")) + '">'
              + esc(i18n.t("decision.write.act")) + "</button>"
            : "")
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
      + '<div class="prose">' + body + "</div>"
      + renderChapterOf(s) + renderFork(s) + renderDeps(s) + renderRejoin(s) + renderPipelineOf(s) + back
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
             code: ($("#ed-code") || {}).value || "",
             // 和 paths/inputs/code 同一档：结构信息，只写进 note.md，译文里一行都没有
             branch: ($("#ed-branch") || {}).value || "extends",
             bnote: ($("#ed-bnote") || {}).value || "",
             decision: ($("#ed-decision") || {}).value || "",
             // 这一步在定稿流程上的例外。控件只在项目真的有流程时才画出来
             // （见 pipelineField），画不出来时这两项恒为空、也不会被发出去。
             pipe: ($("#ed-pipe") || {}).value || "",
             pnote: ($("#ed-pnote") || {}).value || "",
             // 这一步开的那个章节（空 = 不开，沿 parent 继承）
             chapter: ($("#ed-chap") || {}).value || "",
             chnote: ($("#ed-chnote") || {}).value || "", lang: edLang };
  }
  /* 编辑器此刻对着的那一份磁盘内容。译文只有标题和正文——path / input / code
     都是结构信息，翻译文件里一行都不许有（写两份就是双真相源，而且 core 会
     把它们读都不读地丢掉并报一条 translation_structural_key）。 */
  var EMPTY_TARGET = { title: "", body: "", paths: "", inputs: "", code: "",
                       branch: "extends", bnote: "", decision: "", pipe: "", pnote: "",
                       chapter: "", chnote: "" };
  function editTarget(s, l) {
    if (!s) return Object.assign({}, EMPTY_TARGET);
    if (!l) {
      return { title: s.title || "", body: s.body || "", paths: pathsToText(s),
               inputs: inputsToText(s), code: codeToText(s),
               branch: s.branch || "extends", bnote: s.branch_note || "",
               decision: s.decision || "",
               pipe: (s.pipeline || {}).rule || "", pnote: (s.pipeline || {}).note || "",
               /* 框里放的是**这一步自己写的那个名字**，不是它继承来的归属：
                  把继承来的名字预填进去，一按保存就在这一步身上多写了一行
                  ——二十步各一行、章节名改一次要改二十个文件，正是继承要避免的。
                  `declared` 说的是「这一行写在哪」，`name` 说的是「归谁」，
                  两者混用会让整条继承下来的子树看着像未分章。 */
               chapter: (s.chapter && s.chapter.declared) ? s.chapter.name : "",
               chnote: (s.chapter && s.chapter.declared) ? (s.chapter.note || "") : "" };
    }
    var e = (s.tr || {})[l] || {};
    return Object.assign({}, EMPTY_TARGET, { title: e.title || "", body: e.body || "" });
  }
  function sameAsStep(s, st) {
    if (!s || !st) return false;
    var base = editTarget(s, st.lang || "");
    // 老草稿里没有 inputs / code / branch / decision 这几个键：缺的当成默认值比，
    // 否则升级之后每一份旧草稿都会被判成「有未保存改动」，每次进出都弹框。
    return st.title === base.title && st.body === base.body && st.paths === base.paths
      && (st.inputs || "") === base.inputs && (st.code || "") === base.code
      && (st.branch || "extends") === base.branch && (st.bnote || "") === base.bnote
      && (st.decision || "") === base.decision
      && (st.pipe || "") === base.pipe && (st.pnote || "") === base.pnote
      && (st.chapter || "") === base.chapter && (st.chnote || "") === base.chnote;
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

  /* 这一步在定稿流程上的例外（`pipeline: include / exclude`）。三选一，默认那一档
     就是「别动它」——成员是从成果反推出来的，它自己会保持正确。
     **只在项目真的声明了成果时才画这个控件**，而且没画出来时 saveEditor 连这个键
     都不发。理由是数据安全，不是洁癖：forest 里没有 pipeline 这个键时，我们根本
     不知道磁盘上那一行写着什么（`pipeline: exclude` 完全可以先于 `result:` 写下），
     照空值发回去就是替人把他写的那一行删掉，而他不会收到任何提示。 */
  function pipelineField(base) {
    if (!F.pipeline) return "";
    var opt = function (v, key) {
      return '<option value="' + v + '"' + (base.pipe === v ? " selected" : "") + ">"
        + esc(i18n.t(key)) + "</option>";
    };
    return '<div class="edrel">'
      + '<label class="edpaths"><span>' + esc(i18n.t("editor.pipeline.label")) + "</span>"
      + '<select id="ed-pipe">'
      + opt("", "editor.pipeline.auto")
      + opt("include", "editor.pipeline.include")
      + opt("exclude", "editor.pipeline.exclude")
      + "</select></label>"
      + '<label class="edpaths"><span>' + esc(i18n.t("editor.pipeline.note.label")) + "</span>"
      + '<input id="ed-pnote" maxlength="200" value="' + esc(base.pnote) + '" placeholder="'
      + esc(i18n.t("editor.pipeline.note.placeholder")) + '"></label>'
      + "</div>"
      + '<span class="edtip">' + i18n.tHtml("editor.pipeline.hint") + "</span>"
      + '<span class="edtip">' + i18n.tHtml("editor.pipeline.note.required") + "</span>";
  }

  /* ⑨ 这一步开不开一个章节。
   *
   * 这一栏**一直在**（不像 pipelineField 那样要等项目先有流程）：它是这个功能
   * 唯一的发现入口，而人真的想开一条新线时看的正是这里。代价是安全的——
   * 它默认空着，而 saveEditor 只在**值真的变了**时才把 `chapter` 发出去，
   * 所以一次无关的正文编辑绝不会碰到磁盘上那一行。
   *
   * 留空 = 继承 parent 的章节。所以留空时那行灰字要说清它此刻在继承谁的哪一章——
   * 没有这句话，「留空 = 继承」在界面上完全不可见，人就会每一步都填，
   * 而那正好毁掉继承的全部好处（改一次章节名要动二十个文件）。 */
  function chapterField(base, s) {
    var src = base.chapter ? "" : U.chapterSourceOf(IDX, s.id);
    var inh = (!base.chapter && src && CH.of[s.id])
      ? '<span class="edtip">'
        + esc(i18n.t("editor.chapter.inherited", { chapter: CH.of[s.id], id: src })) + "</span>"
      : "";
    return '<div class="edrel">'
      + '<label class="edpaths"><span>' + esc(i18n.t("editor.chapter.label")) + "</span>"
      + '<input id="ed-chap" maxlength="60" value="' + esc(base.chapter) + '" placeholder="'
      + esc(i18n.t("editor.chapter.placeholder")) + '"></label>'
      + '<label class="edpaths"><span>' + esc(i18n.t("editor.chapter.note.label")) + "</span>"
      + '<input id="ed-chnote" maxlength="200" value="' + esc(base.chnote) + '" placeholder="'
      + esc(i18n.t("editor.chapter.note.placeholder")) + '"></label>'
      + "</div>" + inh
      + '<span class="edtip">' + i18n.tHtml("editor.chapter.hint") + "</span>";
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
            + '<span class="edtip">' + i18n.tHtml("editor.code.hint") + "</span></label>"
            /* 这一步和它 parent 之间那条边是什么性质，以及这一步底下那个岔路口
               在决定什么。两件事写在两个地方是有理由的：branch 说的是**这条边**，
               decision 说的是**底下那个岔路口**，把它们混成一个字段就会出现
               「从候选 A 往下走的每一步都变成候选」。 */
            + '<div class="edrel">'
            + '<label class="edpaths"><span>' + esc(i18n.t("editor.branch.label")) + "</span>"
            + '<select id="ed-branch">'
            + '<option value="extends"' + (base.branch !== U.BRANCH_ALT ? " selected" : "") + ">"
            + esc(i18n.t("editor.branch.extends")) + "</option>"
            + '<option value="alternative"' + (base.branch === U.BRANCH_ALT ? " selected" : "") + ">"
            + esc(i18n.t("editor.branch.alternative")) + "</option>"
            + "</select></label>"
            + '<label class="edpaths"><span>' + esc(i18n.t("editor.branch.note.label")) + "</span>"
            + '<input id="ed-bnote" maxlength="200" value="' + esc(base.bnote) + '" placeholder="'
            + esc(i18n.t("editor.branch.note.placeholder")) + '"></label>'
            + "</div>"
            + '<span class="edtip">' + i18n.tHtml("editor.branch.hint") + "</span>"
            + '<label class="edpaths"><span>' + esc(i18n.t("editor.decision.label")) + "</span>"
            + '<input id="ed-decision" maxlength="300" value="' + esc(base.decision) + '" placeholder="'
            + esc(i18n.t("editor.decision.placeholder")) + '">'
            + '<span class="edtip">' + i18n.tHtml("editor.decision.hint") + "</span></label>"
            + chapterField(base, s) + pipelineField(base))
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
    ["#ed-title", "#ed-paths", "#ed-inputs", "#ed-code", "#ed-pnote"].forEach(function (sel) {
      var el = $(sel);
      if (el) el.addEventListener("input", scheduleDraft);
    });
    // 三选一那个下拉不发 input 事件，只发 change。漏了它的话，只改了取值
    // 没改说明的那次编辑不会进草稿，刷新一下就没了。
    var pipeSel = $("#ed-pipe");
    if (pipeSel) pipeSel.addEventListener("change", scheduleDraft);

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

  /* 编辑器/新建框里那两个控件序列化成写入侧收的那一个字符串。
     逐字对着存储格式：`branch: alternative | 说明`，extends 就发空串。 */
  function branchField(st) {
    if (!st || st.branch !== U.BRANCH_ALT) return "";
    var note = String(st.bnote || "").trim();
    return note ? U.BRANCH_ALT + " | " + note : U.BRANCH_ALT;
  }
  /* 同上，`pipeline: <取值> | <理由>`。理由是**必填**的（写入侧会 400）：
     候选组在树上看得见，而这一行除了改变一份导出之外不留任何痕迹，
     没有那半句话就分不清它是想清楚的决定还是一次误点。撤销时说明一起丢掉——
     一行没有取值的 pipeline 读回来什么都不是。 */
  function pipelineFieldValue(st) {
    var rule = String((st && st.pipe) || "").trim();
    if (!rule) return "";
    var note = String((st && st.pnote) || "").trim();
    return note ? rule + " | " + note : rule;
  }
  /* 同上，`chapter: <名字> | <这个章节是什么>`。说明是**可选**的（这是它和
     pipeline 唯一的分歧：一个章节是看得见的，整条子树归了它，名字本身已经说清
     它是什么）；名字空着就发空串 = 撤销声明，回到继承，文件里不留一行空的。 */
  function chapterFieldValue(st) {
    var name = String((st && st.chapter) || "").trim();
    if (!name) return "";
    var note = String((st && st.chnote) || "").trim();
    return note ? name + " | " + note : name;
  }

  function saveEditor() {
    var s = IDX[selected()];
    var st = editorState();
    if (!s || !st) return;
    clearTimeout(draftTimer);
    saveDraftNow();                       // 先钉住：网络失败、409、页面被关都不该让这段字消失
    /* 两条写入路径。译文那条只发 title 和 body：补翻译的工具碰不到原文，
       所以它连 paths / status 都不该有能力发出去。 */
    var payload = {
      title: st.title,
      body: st.body,
      paths: textToPaths(st.paths),
      // 三块结构化文本一起回写。整组替换是对的：框里显示的就是磁盘上的
      // 全部内容（`commit:` 派生出来的那条除外，见 codeToText），
      // 删掉一行的意思就该是删掉那一行。
      inputs: textToPaths(st.inputs),
      code: textToPaths(st.code),
      /* 说明必须跟着 branch 一起发（写入侧没有单独的 branch_note 字段）：
         只改说明、不改 kind 的话，那句说明会挂在一个不存在的候选身份上。
         改回 extends 就发空串——「标错了要能改回来」，和 lang 同一条。 */
      branch: branchField(st),
      decision: (st.decision || "").trim(),
      // 乐观并发控制：expect 是我打开这一步时读到的摘要。这期间别人改过就 409，
      // 由人来判怎么合，而不是谁最后按保存谁赢。
      expect: s.digest || "",
    };
    /* `pipeline` 只在控件真的画出来时才发。控件画不出来（项目还没声明成果）
       就意味着我们不知道磁盘上那一行是什么，而 update_step 只在键**在**的时候
       才动它——不发这个键，别人先写好的 `pipeline: exclude | …` 就不会被
       一次无关的正文编辑悄悄抹掉。 */
    if (F.pipeline && !st.lang) {
      /* 理由必填，**当场拦住**而不是让人走一趟服务端。写入侧确实会 400，但那条
         报错是中文的、而且要等一次往返——人看到的是「保存失败」加一句读不懂的话，
         最可能的反应是再按一次保存。这里问的和写入侧问的是同一件事，所以两处
         判据必须一致：有取值就必须有理由。 */
      if (String(st.pipe || "").trim() && !String(st.pnote || "").trim()) {
        toast(i18n.t("editor.pipeline.note.required"), true);
        var pn = $("#ed-pnote");
        if (pn) pn.focus();
        return;
      }
      payload.pipeline = pipelineFieldValue(st);
    }
    /* `chapter` **只在真的改了的时候才发**。理由和上面 pipeline 那条同源，
       但形状不一样：这一栏一直画得出来，可它有一种我们看不见的磁盘状态——
       `chapter: | 只写了说明没写名字` 那种写坏的行（core 报 bad_chapter、
       归属退回继承、而那半句人写的话原样留在文件里）。它在 forest 里没有任何
       痕迹，照空值发回去就是替人把那半句话删了，而他不会收到任何提示。
       比一次「差值才发」更贵的，只有一次静默的数据丢失。 */
    if (!st.lang) {
      var was = editTarget(s, "");
      if (chapterFieldValue(st) !== chapterFieldValue(was)) {
        payload.chapter = chapterFieldValue(st);
      }
    }
    var go = st.lang
      ? putTranslation(s.id, st.lang, {
          title: st.title, body: st.body,
          expect: trDigest[trKey(s.id, st.lang)] || "",
        }).then(refresh)
      : patch(s.id, payload);
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
  /* preset：拖拽已经把新父节点挑好了，直接填进下拉框（下拉框仍然摆在那儿，
     因为人还得看一眼「我到底挂到哪了」，而且键盘用户走的就是它）。
     dragged：这一次是拖出来的，多说一行「只改了 parent，inputs 一个字没动」。 */
  function openMove(sid, preset, dragged) {
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
    $("#mv-parent").value = preset === undefined || preset === null ? cur : preset;
    $("#mv-reason").value = "";
    $("#mv-dragnote").hidden = !dragged;
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
    }).then(function (info) {
      return refresh().then(function () {
        toast((parent ? i18n.t("toast.moved", { id: sid, parent: parent })
                      : i18n.t("toast.moved.root", { id: sid }))
              + movedForks(info));
      });
    }).catch(fail);
  }

  /* 移动一个**候选**的直接后果：它原来那一组少了一个，落脚的那一组多了一个。
   *
   * 这句话事后再也看不出来——候选组是派生的，重新拉一遍森林只看得到「现在是
   * 什么样」，看不到「刚才这一下改了什么」。不说的话，人下次打开只会看到一条
   * 来路不明的 lone_alternative 提示，还得自己想半天是哪次移动造成的。
   *
   * 数据是 move_step 现算的那一份（服务端把它整个 JSON 回来），这里一条判据都
   * 没重写：组的成员和状态仍然只有 core.compute_branch_groups 一个来源。
   */
  function movedForks(info) {
    var alt = (info || {}).alternatives || {};
    var out = "";
    ["left", "joined"].forEach(function (side) {
      var g = alt[side];
      if (!g) return;
      var n = (g.options || []).length;
      out += " · " + (g.at ? i18n.t("toast.moved.fork", { at: g.at, n: n })
                           : i18n.t("toast.moved.fork.roots", { n: n }));
    });
    /* 换章节也要说。章节是**继承**来的，所以挪一步会把它整条子树一起换掉——
       屏幕上那几步的归属、它们各自算进哪一份 Methods，全都变了，而这一切
       没有任何一行 `chapter:` 被写过（它是派生的）。不说的话，这就是一次
       悄悄发生的结构变动——而 `moved:` 那套审计存在的全部理由就是不许这样。
       服务端的回执里带着，CLI 也早就在打了，只有网页把它丢了。 */
    var ch = (info || {}).chapter;
    if (ch && ch.changed) {
      var from = ch.from || i18n.t("chapter.none");
      var to = ch.to || i18n.t("chapter.none");
      var also = (ch.steps || []).length - 1;      // 减掉被拖的那一步自己
      out += " · " + i18n.t("toast.moved.chapter", { from: from, to: to });
      if (also > 0) out += i18n.t("toast.moved.chapter.also", { n: also });
    }
    return out;
  }

  /* ------------------------------------------------------------ ①b 拖着改父节点
   *
   * 用户的原话是「所以可以自由拖动变换关系吗」——他要的是**手势**，不是表单。
   * 一个 50 步的项目里，「在下拉框里翻 id」这件事本身就贵到让人不想改结构，
   * 于是又绕回「把两步的正文对调」那条一点痕迹都不留的老路上去。
   * 拖拽要省掉的正是翻下拉框的那十几秒，**不是**那句原因。
   *
   * 五条自觉的边界：
   *
   * 1) **手势只挑目标，不代替原因。** 松手之后弹的还是同一个 #dlg-move，新父节点
   *    已经由手势填好，焦点直接落在原因框上。原因是这条审计里唯一无法自动生成的
   *    部分：移动过的树本来就会和创建顺序对不上，半年后只有那一句话解释得了它。
   *    取消 / Esc = 什么都没发生，树回到原样，磁盘上一个字节都没动。
   *
   * 2) **只改 parent，绝不动 inputs。** parent 是「我当时接着哪一步想」，
   *    inputs 是「这些字节从哪来」。所以**数据流视图整个不参与拖拽**：那张图画的
   *    边就是 inputs，在它上面能拖，人立刻会以为自己在改数据依赖，而数据流图
   *    从此就会跟着树形一起骗人。在那张图上按住卡片拖，得到的是一句说明，
   *    不是一次移动。移动对话框上也另起一行把这条界线写出来。
   *
   * 3) **提为根必须是有意的。** 「没落在任何卡片上就算提为根」是最容易误触发的
   *    判定，而误触发的代价是一条永久审计加一句被逼出来的原因。所以提为根有一条
   *    **明确的落区**：拖起来之后左上角才出现，只有落在那条上才作数；落在别的
   *    空白处等于取消。
   *
   * 4) **非法落点在拖动过程中就禁掉**，判断全部转手给 U.moveError——和对话框问的
   *    是同一个函数。自己、自己的后代、当前的父，指针经过时既不高亮也接不住，
   *    跟手的小标签当场说明是为什么。
   *
   * 5) **拖拽是增强，不是唯一入口。** 详情面板上那个「⇄ 移动」按钮 + 对话框那条路
   *    一个字都没改：只能靠拖的功能，对键盘用户等于不存在。
   *
   * 视觉：规格里「一个视觉通道只承载一件事」在这里是硬约束。线型已经归 status，
   * 不透明度归祖先链/搜索命中，颜色只作线型的补强，字形标记（🖼 📎 L0 ↺✕ ⇄）
   * 那一档也满了。所以拖拽用的是三样**此前没人用过**的东西：
   *   · 卡片外面那一圈 outline —— 它画在 border 之外，不是 border，
   *     所以既不改线型也不改颜色；细圈 = 跟着走的那一片，粗圈 = 接得住的新父。
   *   · 跟着指针走的一个小标签 —— 只在拖动期间存在的浮层。
   *   · 顶上那条落区 —— 同样只在拖动期间存在。
   * 三样东西在松手的一刻全部消失，静止画面上一个像素都没变。
   */

  var drag = null;

  function viewCards() {
    if (view === "graph") return "#dnodes .card";
    if (view === "list") return "#rows .row";
    return "#fnodes .fcard";
  }

  /* 当前视图里每张卡片在**布局坐标**里的矩形。图视图的坐标是 F.tree 早算好的，
     不去问 DOM 量尺寸：拖动中问 DOM 就得走一遍布局，几百个节点会卡手。 */
  function dragRects() {
    var T = F.tree || { nodes: {} };
    var out = [];
    F.steps.forEach(function (s) {
      var n = T.nodes[s.id];
      if (n) out.push({ id: s.id, x: n.x, y: n.y, w: T.node_w, h: T.node_h });
    });
    return out;
  }

  /* 指针位置 → 落在哪一步。图视图换算掉缩放，列表视图除以行高。
     返回空串 = 没落在任何一步上。

     **先过一道视口闸门**：\#diagram / \#rows 的矩形在滚动时会伸到视口外面去，
     光做坐标换算的话，指针明明停在顶栏的搜索框上、或者右边的详情面板上，
     算出来却是画布里某个**屏幕上根本看不见**的节点——松手就是一次挂到看不见的
     地方的移动，而移动会写一条永久的审计记录。
     所以判据不是"换算完落在哪个矩形里"，而是"指针是不是真的在这块可视区域上"。 */
  function inScroller(cx, cy) {
    return U.withinRect($("#scroller").getBoundingClientRect(), cx, cy);
  }

  function aimAt(cx, cy) {
    if (!inScroller(cx, cy)) return "";
    if (view === "graph") {
      var box = $("#diagram").getBoundingClientRect();
      return U.hitRect(drag.rects, (cx - box.left) / zoom, (cy - box.top) / zoom);
    }
    var rb = $("#rows").getBoundingClientRect();
    if (cx < rb.left || cx > rb.right) return "";
    var i = U.rowAt(cy - rb.top, F.row_h || 28, F.steps.length);
    return i < 0 ? "" : F.steps[i].id;
  }

  function onDragDown(e) {
    if (drag || $("#dlg-move").open) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    var hit = e.target.closest("#dnodes .card, #rows .row, #fnodes .fcard");
    if (!hit) return;
    var id = hit.getAttribute("data-id");
    if (!IDX[id]) return;
    // 三种按下都先记下来：只读和数据流那两种也要等到人**真的拖了**才说话，
    // 按一下就弹一句话等于把「选中一个节点」变成一次说教。
    var kind = !canWrite() ? "ro" : (view === "flow" ? "flow" : "live");
    drag = { id: id, kind: kind, x0: e.clientX, y0: e.clientY, on: false,
             armed: e.pointerType !== "touch", timer: 0,
             target: "", root: false, code: "", ok: false, moving: [], rects: [] };
    /* 触屏上「按住不动」和「往下滑一屏」在按下的那一刻分不开，而滑动才是这一页
       在手机上最常做的事。所以触屏要长按 400ms 才起拖，其余时候照常滚。
       按钮 + 对话框那条路一直在，触屏用户不会因此失去移动能力。 */
    if (!drag.armed) {
      drag.timer = setTimeout(function () {
        if (!drag || drag.on || drag.kind !== "live") return;
        drag.armed = true;
        beginDrag();
        aimDrag(drag.x0, drag.y0);
      }, 400);
    }
  }

  function onDragMove(e) {
    if (!drag) return;
    if (!drag.on) {
      if (!U.beyondSlop(e.clientX - drag.x0, e.clientY - drag.y0)) return;
      clearTimeout(drag.timer);
      // 拖不动的两种情形，到这里才说——而且说的是**为什么**，不是「不允许」。
      if (drag.kind === "ro") { toast(i18n.t("drag.readonly")); endDrag(); return; }
      if (drag.kind === "flow") { toast(i18n.t("drag.flow")); endDrag(); return; }
      if (!drag.armed) { endDrag(); return; }   // 触屏没长按：这一下是滑动，让位给滚动
      beginDrag();
    }
    e.preventDefault();                          // 拖动中不选中文字、不滚动
    aimDrag(e.clientX, e.clientY);
  }

  function beginDrag() {
    drag.on = true;
    drag.moving = U.subtreeIds(IDX, drag.id);
    drag.rects = dragRects();
    var mv = Object.create(null);
    drag.moving.forEach(function (k) { mv[k] = 1; });
    document.body.classList.add("dragging");
    document.querySelectorAll(viewCards()).forEach(function (el) {
      el.classList.toggle("dsub", !!mv[el.getAttribute("data-id")]);
    });
    var rz = $("#droot");
    rz.hidden = false;
    // 本来就是根的步骤没有「提为根」可言，落区如实地不接受，而不是接了再报错
    rz.classList.toggle("off", U.moveError(IDX, drag.id, "") !== "");
    $("#dg-id").textContent = drag.id;
    $("#dghost").hidden = false;
  }

  function aimDrag(cx, cy) {
    var rz = $("#droot").getBoundingClientRect();
    var onRoot = !$("#droot").hidden
      && cx >= rz.left && cx <= rz.right && cy >= rz.top && cy <= rz.bottom;
    var target = onRoot ? "" : aimAt(cx, cy);
    // 没落在卡片上、也没落在落区上：这是「空白」，它既不是提为根也不是错误。
    var code = (onRoot || target) ? U.moveError(IDX, drag.id, target) : "away";
    drag.root = onRoot;
    drag.target = target;
    drag.code = code;
    drag.ok = code === "";
    $("#droot").classList.toggle("on", onRoot && drag.ok);
    document.querySelectorAll(viewCards()).forEach(function (el) {
      el.classList.toggle("dtarget", drag.ok && !onRoot && el.getAttribute("data-id") === target);
    });
    paintGhost(cx, cy);
  }

  /* 跟手的小标签。它同时回答三件事：拖的是谁、要落到哪、跟着走的有几步。
     第三件是最容易被忽略、后果又最大的一件——拖一棵二十步的子树和拖一个光杆
     节点在屏幕上长得一样。 */
  function paintGhost(cx, cy) {
    var g = $("#dghost"), carried = drag.moving.length - 1, what;
    if (drag.ok && drag.root) what = i18n.t("drag.aim.root");
    else if (drag.ok) what = i18n.t("drag.aim.parent", { parent: drag.target });
    else if (drag.code === "away") what = i18n.t("drag.aim.none");
    else if (drag.code === "self") what = i18n.t("drag.no.self");
    else if (drag.code === "descendant") what = i18n.t("drag.no.descendant", { id: drag.id, parent: drag.target });
    else if (drag.code === "noop") what = i18n.t("drag.no.noop");
    else what = i18n.t("drag.no.missing");
    $("#dg-what").textContent = what;
    $("#dg-carry").textContent = carried > 0 ? i18n.t("drag.carry", { n: carried }) : "";
    g.classList.toggle("bad", !drag.ok);
    g.style.left = (cx + 14) + "px";
    g.style.top = (cy + 18) + "px";
  }

  function onDragUp() {
    if (!drag) return;
    var d = drag;
    endDrag();
    if (!d.on) return;
    // 起拖之后的那一次 click 不能再当成「选中」：一次移动会紧接着弹框，
    // 而框背后的选中态跳走会让人以为自己点错了地方。
    swallowNextClick();
    if (!d.ok) return;        // 空白 / 非法落点：什么都没发生，树一个字没动
    openMove(d.id, d.root ? "" : d.target, true);
  }

  function swallowNextClick() {
    var off = function () { document.removeEventListener("click", once, true); clearTimeout(t); };
    var once = function (ev) { ev.stopPropagation(); ev.preventDefault(); off(); };
    var t = setTimeout(off, 400);   // 没有跟上来的 click 就自己拆掉，别吃掉下一次点击
    document.addEventListener("click", once, true);
  }

  function endDrag() {
    if (!drag) return;
    clearTimeout(drag.timer);
    if (drag.on) {
      document.body.classList.remove("dragging");
      document.querySelectorAll(".dsub, .dtarget").forEach(function (el) {
        el.classList.remove("dsub", "dtarget");
      });
      $("#droot").hidden = true;
      $("#droot").classList.remove("on", "off");
      $("#dghost").hidden = true;
    }
    drag = null;
  }

  /* 中途取消：Esc、指针被系统收走（来电、触屏改成滚动）、窗口失焦。
     三条出口都必须把树恢复原样——半截的高亮留在屏幕上比没有高亮更糟。 */
  function cancelDrag(say) {
    if (!drag) return;
    var was = drag.on;
    endDrag();
    if (was && say) toast(i18n.t("drag.cancelled"));
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
              branch: $("#nf-branch").value, bnote: $("#nf-bnote").value,
              decision: $("#nf-decision").value,
              chapter: $("#nf-chap").value, chnote: $("#nf-chnote").value,
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
    /* **branch / decision 一个字都不继承。** branch 说的是「我和我 parent 之间
       那条边」，decision 说的是「我底下那个岔路口」——抄下去的结果是从候选 A
       往下走的每一步都变成候选，一棵树上到处是假岔路口。 */
    $("#nf-branch").value = "extends";
    $("#nf-bnote").value = "";
    $("#nf-decision").value = "";
    /* **章节也一个字都不继承**，而且理由更硬：章节本来就是沿树继承的，把父步骤
       那个名字预填进来，等于把一次继承展开成一份会过期的拷贝——父步骤改个章节名，
       这一步就留在原来那一章里，而磁盘上谁都看不出这是抄来的。
       开一条新线是显式动作（填这一栏），留空就是「跟着上面那条线走」。 */
    $("#nf-chap").value = "";
    $("#nf-chnote").value = "";

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
    $("#nf-branch").value = d.branch || "extends";
    $("#nf-bnote").value = d.bnote || "";
    $("#nf-decision").value = d.decision || "";
    $("#nf-chap").value = d.chapter || "";
    $("#nf-chnote").value = d.chnote || "";
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
    var wantBranch = branchField({ branch: $("#nf-branch").value, bnote: $("#nf-bnote").value });
    var wantDecision = $("#nf-decision").value.trim();
    var wantChapter = chapterFieldValue({ chapter: $("#nf-chap").value, chnote: $("#nf-chnote").value });
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
        branch: wantBranch,
        decision: wantDecision,
        // 开一条新线多半就是「建这一步」的同一次动作（消融的头一步），
        // 逼人建完再改一次，这一行大概率就永远空着了——和 decision 同一个道理。
        chapter: wantChapter,
      }),
    }).then(function (step) {
      dropDraft(NEW_DRAFT_ID);
      /* 建步骤那条路由**曾经**不把 inputs / code 透传给写入层，`branch` /
         `decision` 刚加上时也一样。少写几个字段是静默丢数据——201 回来了，
         磁盘上没有。判据看的是**返回的那一步身上有没有**，所以今天的服务端
         （四个键全透传）走到这里 lack 就是 false，一次多余的请求都不会发；
         对着一台还没升级的服务端（静态部署、旧的远端后端）它才补那一次 PATCH。
         删掉它就等于让「建步骤时顺手写清这是哪条候选」在旧服务端上无声失效。 */
      var lack = (wantInputs.length && !(step.inputs || []).length)
        || (wantCode.length && !(step.code || []).filter(function (c) { return c.from !== "commit"; }).length)
        || (wantBranch && step.branch !== U.BRANCH_ALT)
        || (wantDecision && !(step.decision || ""));
      /* 章节这一项判不出来：`chapter` **刻意不进 to_dict()**（一个 `chapter:` 都
         没写的项目不该多出一个字段值），所以建步骤那条路由回来的这份里根本没有它。
         判不出来就补一次——只在人真的填了这一栏时才多发这一个请求，而这一栏
         绝大多数时候是空的。服务端已经透传了的话，这次 PATCH 写回去的是同一行字。 */
      var go = (lack || wantChapter)
        ? papi("/steps/" + encodeURIComponent(step.id), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ inputs: wantInputs, code: wantCode,
                                   branch: wantBranch, decision: wantDecision,
                                   chapter: wantChapter }),
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
        if ($("#ed-branch")) $("#ed-branch").value = dd.branch || "extends";
        if ($("#ed-bnote")) $("#ed-bnote").value = dd.bnote || "";
        if ($("#ed-decision")) $("#ed-decision").value = dd.decision || "";
        if ($("#ed-pipe")) $("#ed-pipe").value = dd.pipe || "";
        if ($("#ed-pnote")) $("#ed-pnote").value = dd.pnote || "";
        if ($("#ed-chap")) $("#ed-chap").value = dd.chapter || "";
        if ($("#ed-chnote")) $("#ed-chnote").value = dd.chnote || "";
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

    /* 定稿流程 → 开发路径。这是「两条路径都留着」的全部意义：这一步当时有几个
       候选、为什么选了它，只有那一侧答得出。所以它换模式、选中、滚过去，
       三件事一起做完——只跳不选的话，人到了那边还得自己在树上找。 */
    var dg = e.target.closest("[data-devgoto]");
    if (dg) {
      e.preventDefault();
      setMode("dev", true);
      select(dg.getAttribute("data-devgoto"));
      scrollToSelected();
      return;
    }
    // 反方向：开发路径 → 定稿流程里的那一步。
    var pg = e.target.closest("[data-pipego]");
    if (pg) {
      e.preventDefault();
      setMode("pipeline", true);
      pipeScrollTo(pg.getAttribute("data-pipego"));
      return;
    }
    // 定稿流程内部的跳转（成果清单、最弱的那一环）：不换模式，只滚过去。
    var pj = e.target.closest("[data-pgoto]");
    if (pj) { e.preventDefault(); pipeScrollTo(pj.getAttribute("data-pgoto")); return; }

    // 「只看这一章」。点的是章节清单里那一行，做的事和顶栏那个筛选器一模一样
    // ——同一个状态、同一份判据，界面上两个入口。
    var cg = e.target.closest("[data-chapgo]");
    if (cg) { e.preventDefault(); setChapFilter(cg.getAttribute("data-chapgo")); return; }

    /* 导出。**故意不 preventDefault**：下载是那个 <a download> 自己的事，
       拦下来就得在这一页里再造一份字节，而「只有一份实现」正是这一档的规矩。 */
    var ex = e.target.closest("[data-export]");
    if (ex) { doExport(ex.getAttribute("data-export"), ex.getAttribute("data-expchap")); return; }

    var goto = e.target.closest("[data-goto]");
    if (goto) { e.preventDefault(); select(goto.getAttribute("data-goto")); scrollToSelected(); return; }

    var hit = e.target.closest("#rows .row, #dnodes .card, #fnodes .fcard");
    if (hit) { select(hit.getAttribute("data-id")); return; }

    // 点图上的空白处 = 取消选中 = 所有节点回到全亮。
    // 不透明度这个通道只承载"是否在选中的祖先链上"，没有选中时就不该有任何东西是淡的。
    // 定稿流程那一档不参与选中（它是一份文档，不是一棵可以戳的树），在它上面
    // 点一下不该把开发路径那边选好的步骤悄悄清掉——切回去时人会以为选择丢了。
    if (mode === "dev" && e.target.closest("#scroller") && selected()) { select(""); return; }

    var mt = e.target.closest("#modetoggle button");
    if (mt) { setMode(mt.getAttribute("data-mode")); return; }

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
    /* 声明 / 撤回一个成果。这是**唯一**写下来的那件事，所以它有自己的入口，
       不混进「改个标题」那条随手的路：它重写的是整条定稿流程、Methods 里出现
       哪几步、导出的那张图长什么样。撤回不问原因——它不销毁任何事实，
       那一步和它的记录一个字都没动，开发路径上它还在原处。 */
    if (name === "result" || name === "result-mark") {
      var rs = IDX[selected()];
      if (!rs) { toast(i18n.t("toast.select.step"), true); return; }
      if (name === "result" && rs.pipeline && rs.pipeline.result) {
        papi("/results/" + encodeURIComponent(rs.id), { method: "DELETE" })
          .then(refresh)
          .then(function () { toast(i18n.t("toast.result.unmarked", { id: rs.id })); })
          .catch(fail);
        return;
      }
      var note = prompt(i18n.t("pipeline.result.prompt"), stepTitle(rs));
      if (note === null) return;
      /* 按 **id 发增删**（PUT /results/{id}），不是「整组提交成果列表」：用打开
         页面那一刻的旧列表整组提交，会静默删掉这期间 agent 刚声明的那一条。
         写入侧刻意没有整组替换那条路，服务端也只开了按 id 的 PUT / DELETE。 */
      papi("/results/" + encodeURIComponent(rs.id), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: note.trim() }),
      }).then(refresh)
        .then(function () { toast(i18n.t("toast.result.marked", { id: rs.id })); })
        .catch(fail);
      return;
    }
    /* ⑨ 开一个章节 / 不开。落盘的**只有这一步自己那一行 `chapter:`**——
       底下那些步一个字都不写，它们的归属是沿 parent 继承算出来的。
       所以这里永远不会有一个「把这些步都标成消融」的批量按钮：那会在二十个文件里
       各留一行会过期的拷贝，改一次章节名要改二十处，而「章节说明归谁」也会被
       二十份同名声明搅浑。 */
    if (name === "chapter") {
      var cs = IDX[selected()];
      if (!cs) return;
      var had = !!(cs.chapter && cs.chapter.declared);
      if (had) {
        patch(cs.id, { chapter: "", expect: cs.digest || "" })
          .then(function () { toast(i18n.t("toast.chapter.cleared", { id: cs.id })); })
          .catch(fail);
        return;
      }
      var nm = prompt(i18n.t("chapter.set.prompt"), "");
      if (nm === null || !nm.trim()) return;
      // 跟着一起换章的有几步——**在写之前**数：换章磁盘上一个字节都不变，
      // 二十步集体转过去，diff 里只有这一行。不说的话没人会发现。
      var carry = U.chapterCarry(IDX, cs.id).length;
      patch(cs.id, { chapter: nm.trim(), expect: cs.digest || "" })
        .then(function () {
          toast(i18n.t("toast.chapter.set", { id: cs.id, chapter: nm.trim() })
            + (carry ? " · " + i18n.t("toast.chapter.carry", { n: carry }) : ""));
        }).catch(fail);
      return;
    }
    /* 「这个章节是什么」。它跟着**章节**走，不跟着这一步走——所以它写在
       core 裁定生效的那一处（id 序最早的那个带说明的声明），而不是随手写在
       正在看的这一步身上：写在别处的话，屏幕上显示的还是原来那一句，
       人会以为没保存成功。 */
    if (name === "chapter-note") {
      var ns = IDX[selected()];
      var cname = ns && CH.of[ns.id];
      var entry = cname && CH.byName[cname];
      if (!entry) return;
      /* **永远写到那句生效的说明所在的那一步**，哪怕你正站在另一个声明者身上。
         以前这里是「自己声明过就写自己」——于是在 006 上改（004 才是生效的那个）
         写进了 006、面板照旧显示 004 的旧句子，toast 还说保存成功。
         「改了等于没改，而且它说改了」是所有 bug 里最气人的一种。 */
      var home = U.chapterNoteHome(entry, IDX);
      var hs = IDX[home];
      if (!hs) return;
      var txt = prompt(i18n.t("chapter.write.prompt"), entry.note || "");
      if (txt === null) return;
      var own = (hs.chapter && hs.chapter.name) || cname;
      patch(home, { chapter: txt.trim() ? own + " | " + txt.trim() : own,
                    expect: hs.digest || "" })
        .then(function () {
          // 落在别人身上时把那一步说出来：一次写入改的不是你选中的那个文件，
          // 不说的话下次 diff 会莫名其妙。
          toast(home === ns.id
            ? i18n.t("toast.chapter.desc.saved", { chapter: cname })
            : i18n.t("toast.chapter.desc.saved.elsewhere", { chapter: cname, id: home }));
        })
        .catch(fail);
      return;
    }
    if (name === "edit-insights") { openInsightEditor(); return; }
    if (name === "save-insights") { saveInsights(); return; }
    if (name === "move") { openMove(selected()); return; }
    /* 标成 / 取消互斥候选。写的只有这一步自己那一行 `branch:`。
       **界面上永远不会有一个「标记赢家」按钮**：那个按钮就是双真相源——
       「选了哪个」是从其余候选标 dead 派生出来的，另存一份迟早会和 status 打架。 */
    if (name === "branch") {
      var bs = IDX[selected()];
      if (!bs) return;
      var toAlt = bs.branch !== U.BRANCH_ALT;
      patch(bs.id, { branch: toAlt ? U.BRANCH_ALT : "", expect: bs.digest || "" })
        .then(function () {
          var g = U.groupOf(F.branch_groups, IDX[bs.id] || {});
          toast(toAlt
            ? i18n.t("toast.branch.alternative",
                     { id: bs.id, n: g ? (g.options || []).length : 1, parent: bs.parent || "" })
            : i18n.t("toast.branch.extends", { id: bs.id }));
        }).catch(fail);
      return;
    }
    /* 「在决定什么」。这句话推导不出来——候选有谁、选中了谁都算得出来，唯独它
       只能人写，和「为什么」是同一类字段。所以它是一个 prompt，不是一个开关。 */
    if (name === "decision") {
      var qs = IDX[selected()];
      if (!qs) return;
      var q = prompt(i18n.t("decision.write.prompt"), qs.decision || "");
      if (q === null) return;
      patch(qs.id, { decision: q.trim(), expect: qs.digest || "" })
        .then(function () {
          toast(i18n.t(q.trim() ? "toast.decision.saved" : "toast.decision.cleared", { id: qs.id }));
        }).catch(fail);
      return;
    }
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
          /* 三条后果，按**严重程度**排，只说最重的那一条：
             ① 被删的这一步是声明出来的**成果** —— 整条定稿流程从它长出来，
                它一没，流程静默变空，而 id 会被重用，下一个拿到该号的步骤会
                无声地变成论文报的那个结果；
             ② 指空的 `input:` —— 可溯源链断在这里，而它在界面上只是一行小字；
             ③ 孤儿 —— 会被降级为根，在图上仍然看得见，最不容易被忽略。
             说三条等于一条都没说：toast 只有几秒，人只读得进第一句。 */
          var lostResult = !!(info.dangling_results && info.dangling_results.length);
          toast(lostResult
            ? i18n.t("toast.deleted.result", { id: info.id })
            : (info.dangling_inputs && info.dangling_inputs.length
              ? i18n.t("toast.deleted.inputs", { id: info.id, ids: info.dangling_inputs.join(" · ") })
              : (info.orphaned.length
                ? i18n.t("toast.deleted.orphaned", { id: info.id, ids: info.orphaned.join(" · ") })
                : i18n.t("toast.deleted", { id: info.id }))),
            // 成果那一条按「要人动手」的样式显示（多停几秒）：它是三条里唯一
            // 会让一份要交出去的产物变空的。
            lostResult);
        });
      }).catch(fail);
    }
    else if (name === "save") { saveEditor(); }
  });

  $("#proj").addEventListener("change", function (e) {
    location.href = e.target.value ? projectHref(e.target.value) : homeHref();
  });
  /* 章节筛选换的是**看多大范围**，不是看哪一份东西、也不是怎么画：
     开发路径那三张图上它只 dim（形状一个像素不变），定稿流程那一档它换的是
     「编哪一条流程」。一个控件，两处含义都对得上——因为它横切在那两级之上。 */
  function setChapFilter(v) {
    chapFilter = v || "";
    var box = $("#chapfilter");
    if (box && box.value !== chapFilter) box.value = chapFilter;
    renderSelection();
    // 定稿流程那一档换的是编哪一条流程，所以得重画；开发路径那三张图只是
    // 换了谁淡谁亮（renderSelection 已经做完），布局一个数都没动。
    if (mode === "pipeline") renderPipeline();
  }
  $("#chapfilter").addEventListener("change", function (e) { setChapFilter(e.target.value); });
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
  ["#nf-title", "#nf-body", "#nf-paths", "#nf-inputs", "#nf-commit", "#nf-code",
   "#nf-bnote", "#nf-decision"].forEach(function (sel) {
    var el = $(sel);
    if (el) el.addEventListener("input", saveNewDraft);
  });
  $("#nf-branch").addEventListener("change", saveNewDraft);
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

  /* ①b 拖着改父节点。用的是 Pointer Events，不是 HTML5 的 drag-and-drop：
     后者在自定义的 SVG / 绝对定位画布上各浏览器表现不一，拖影没法控制，
     触屏基本不可用，而这一页的图视图恰恰就是绝对定位的画布。
     pointermove / pointerup 挂在 document 上而不是卡片上：指针出了卡片
     （拖到空白、拖出窗口）之后事件仍然要收得到，否则松手时高亮会卡在屏幕上。 */
  $("#scroller").addEventListener("pointerdown", onDragDown);
  document.addEventListener("pointermove", onDragMove, { passive: false });
  document.addEventListener("pointerup", onDragUp);
  document.addEventListener("pointercancel", function () { cancelDrag(false); });
  window.addEventListener("blur", function () { cancelDrag(false); });

  // Ctrl/⌘ + 滚轮缩放图视图
  $("#scroller").addEventListener("wheel", function (e) {
    if (!(e.ctrlKey || e.metaKey) || view !== "graph") return;
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });

  document.addEventListener("keydown", function (e) {
    /* 拖到一半按 Esc = 什么都没发生。它排在最前面：拖动期间屏幕上有三样临时的
       东西（跟手标签、落区、两种 outline），任何一条别的分支先 return 掉，
       那三样就会留在屏幕上，而树看起来像是坏了。 */
    if (drag && drag.on && e.key === "Escape") { e.preventDefault(); cancelDrag(true); return; }
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
      /* 三个视图轮着来。数据流也进这个循环——它不是一个附属面板，而是同一份
         文件的第三种读法，和图/列表平级。
         定稿流程**不**进这个循环：它不是第四种读法，是另一批步骤。在那一档上
         按 g 的意思只能是「回去看开发路径」，把它排进循环等于用同一个键说两件事。 */
      if (mode !== "dev") { setMode("dev"); return; }
      view = VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length];
      localStorage.setItem("trace.view", view);
      applyView(); renderSelection(); scrollToSelected();
    } else if (e.key === "p") {
      // 两条路径来回切。和 g 分开是因为它们不是同一层的选择（见 index.html 的
      // #modeswitch 那段）：g 换画法，p 换看的是哪一份东西。
      e.preventDefault();
      setMode(mode === "dev" ? "pipeline" : "dev");
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
    // 章节筛选器里的选项文字（「所有章节」「3 步」）是这一页自己拼的，
    // data-i18n 那一趟刷不到它们 —— 不重画的话切完语言那一栏还留着旧语言。
    renderChapFilter();
    renderWarnings();        // 警告栏和缺失横幅现在都是本语言的说法，切换要跟上
    renderMissingPaths();
    // 定稿流程整块（含那张图上的字）都是本语言的，而且导出就是屏幕上这一份
    renderPipeline();
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
  /* 「这些卡片抓得动」是一条只能靠光标说的话：静态导出里它们仍然点得开、
     仍然能读，但抓不动。所以抓手光标只在写得进去的时候给——给了却抓不动，
     比不给更让人以为是坏了。 */
  document.body.classList.toggle("canwrite", canWrite());
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
        /* 别人刚写进来一步，树的形状可能已经变了——而拖动中的落点判定用的是
           起拖那一刻的坐标。不掐掉的话，重画会把高亮抹掉，人手上却还举着一个
           指向旧位置的落点，松手落到的是另一步。移动会写下永久审计，这种
           「看着 A 落到 B」绝不能发生，所以有拖动在进行时先取消它。 */
        if (drag && drag.on) cancelDrag(true);
        if (PROJECT && !editing) refresh();   // 正在编辑时不要把用户的输入冲掉
      }
    };
    // git 是防抖提交的（默认 45 秒），版本号不变也可能刚刚 push 失败，所以另外轮询。
    refreshGit();
    setInterval(refreshGit, 20000);
  }
})();

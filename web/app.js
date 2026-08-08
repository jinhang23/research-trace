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

  var BASE = window.TRACE_BASE || "";
  var MODE = window.TRACE_MODE === "static" ? "static" : "server";
  var PROJECT = window.TRACE_PROJECT || "";
  var esc = window.md.esc;
  var $ = function (s) { return document.querySelector(s); };

  var F = { steps: [], order: [], lanes: {}, lane_count: 0, warnings: [], row_h: 28, lane_w: 14,
            tree: { nodes: {}, w: 0, h: 0, node_w: 176, node_h: 58 } };
  var IDX = {};
  var PROJECTS = [];
  var query = "";
  var editing = false;
  var view = localStorage.getItem("trace.view") === "list" ? "list" : "graph";
  var zoom = 1;

  var TEMPLATE = "## 为什么\n\n\n## 做了什么\n\n\n## 结果\n\n\n## 结论\n\n\n## 下一步\n";
  var IMG = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
  var RAIL_PAD = 12;

  /* -------------------------------------------------------------- 工具 */

  function token() { return localStorage.getItem("trace.token") || ""; }
  function setToken(t) {
    if (t) localStorage.setItem("trace.token", t); else localStorage.removeItem("trace.token");
    paintToken();
  }
  function paintToken() {
    var b = $("#btn-token");
    b.textContent = token() ? "🔓" : "🔒";
    b.title = token() ? "已设置写入令牌（点击更换或清除）" : "未设置写入令牌 — 只能浏览";
  }
  function canWrite() { return MODE === "server"; }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (token()) opts.headers["Authorization"] = "Bearer " + token();
    return fetch(BASE + path, opts).then(function (r) {
      return r.text().then(function (t) {
        var j = {};
        try { j = t ? JSON.parse(t) : {}; } catch (e) { j = { error: t.slice(0, 200) }; }
        if (!r.ok) throw new Error(j.error || r.status + " " + r.statusText);
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
    if (MODE !== "static") return BASE + "/";
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
  function human(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function fileURL(s, rel) {
    var p = rel.split("/").map(encodeURIComponent).join("/");
    if (MODE === "static") return "steps/" + encodeURIComponent(s.dirname) + "/" + p;
    return BASE + "/p/" + encodeURIComponent(PROJECT) + "/files/" + encodeURIComponent(s.id) + "/" + p;
  }
  function resolverFor(s) { return function (h) { return fileURL(s, h); }; }
  function isAgent(s) { return (s.author || "").indexOf("agent") === 0; }

  function selected() { var h = decodeURIComponent(location.hash.slice(1)); return IDX[h] ? h : ""; }
  function select(id) {
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

  function renderProjects() {
    $("#proj").innerHTML = '<option value="">所有项目 ▸</option>'
      + PROJECTS.map(function (p) {
          return '<option value="' + esc(p.slug) + '"' + (p.slug === PROJECT ? " selected" : "") + ">"
            + esc(p.name) + "（" + p.steps + "）</option>";
        }).join("");
  }

  function renderHome() {
    $("#cards").innerHTML = PROJECTS.map(function (p) {
      var c = p.counts || {};
      var bar = p.steps
        ? ["done", "wip", "dead"].map(function (k) {
            return (c[k] || 0) ? '<i class="sg-' + k + '" style="flex:' + c[k] + '"></i>' : "";
          }).join("")
        : '<i class="sg-empty" style="flex:1"></i>';
      return '<a class="pcard" href="' + projectHref(p.slug) + '">'
        + "<h2>" + esc(p.name) + "</h2>"
        + '<div class="pbar">' + bar + "</div>"
        + '<div class="pmeta mono">' + p.steps + " 步 · done " + (c.done || 0)
        + " / wip " + (c.wip || 0) + " / dead " + (c.dead || 0)
        + (p.latest ? " · 最近 " + esc(p.latest) : "") + "</div>"
        + (p.warnings ? '<div class="pwarn">⚠ ' + p.warnings + " 条警告</div>" : "")
        + "</a>";
    }).join("") || '<p class="placeholder">还没有项目。点右上角新建一个。</p>';
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
    renderWarnings();
    applyView();
    onHash();
  }
  function refresh() { return papi("/forest").then(apply).catch(fail); }
  function refreshProjects() {
    return api("/api/projects").then(function (d) {
      PROJECTS = d.projects || [];
      renderProjects();
      if (!PROJECT) renderHome();
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
      var marks = (pics ? '<span class="cmk" title="' + pics + ' 张图">🖼' + (pics > 1 ? pics : "") + "</span>" : "")
        + (other ? '<span class="cmk" title="' + other + ' 个附件">📎' + (other > 1 ? other : "") + "</span>" : "");
      return '<div class="card s-' + s.status + '" data-id="' + esc(s.id) + '" tabindex="0"'
        + ' style="left:' + n.x + "px;top:" + n.y + "px;width:" + NW + "px;height:" + NH + 'px">'
        + '<div class="chead"><span class="cid">' + esc(s.id) + "</span>"
        + '<span class="cst">' + s.status + "</span>"
        + (isAgent(s) ? '<span class="cbot" title="' + esc(s.author) + '">🤖</span>' : "")
        + marks
        + '<span class="cdate">' + esc(s.date || "") + "</span></div>"
        + '<div class="ctitle">' + esc(s.title || "(无标题)") + "</div>"
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
        + '<span class="t">' + esc(s.title || "(无标题)") + "</span>"
        + (pics ? '<span class="who" title="' + pics + ' 个附件">📎</span>' : "")
        + (isAgent(s) ? '<span class="who" title="' + esc(s.author) + '">🤖</span>' : "")
        + '<span class="d">' + esc(s.date || "") + "</span>"
        + "</div>";
    }).join("");
  }

  function renderWarnings() {
    var bar = $("#warnbar"), ws = F.warnings || [];
    bar.hidden = !ws.length;
    if (!ws.length) return;
    bar.innerHTML = ws.map(function (w) {
      return (w.level === "error" ? "✕ " : "⚠ ") + "<b>" + esc(w.where || w.code) + "</b> — " + esc(w.message);
    }).join("<br>");
  }

  function applyView() {
    $("#dwrap").hidden = view !== "graph";
    $("#track").hidden = view !== "list";
    $("#empty").hidden = F.steps.length > 0;
    $("#zoombar").hidden = view !== "graph";
    document.querySelectorAll("#viewtoggle button").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-view") === view);
    });
    $("#legend").hidden = !F.steps.length;
  }

  /* -------------------------------------------------------------- 选中态 */

  function renderSelection() {
    var sel = selected(), chain = chainOf(sel);
    var q = query.trim().toLowerCase(), hits = 0;

    document.querySelectorAll("#rows .row, #dnodes .card").forEach(function (el) {
      var id = el.getAttribute("data-id"), s = IDX[id];
      if (!s) return;
      var hay = (id + " " + (s.title || "") + " " + (s.body || "") + " " + (s.tags || []).join(" ")).toLowerCase();
      var hit = !q || hay.indexOf(q) >= 0;
      if (q && hit && el.classList.contains("row")) hits++;
      el.classList.toggle("miss", !!q && !hit);
      el.classList.toggle("faded", !!sel && !chain[id]);
      el.classList.toggle("sel", id === sel);
    });
    $("#hits").textContent = q ? hits + " / " + F.steps.length : (F.steps.length ? F.steps.length + " 步" : "");

    document.querySelectorAll("#rails [data-id], #dedges [data-id]").forEach(function (el) {
      var id = el.getAttribute("data-id");
      el.classList.toggle("faded", !!sel && !chain[id]);
      el.classList.toggle("sel", id === sel && el.classList.contains("node"));
    });
  }

  function scrollToSelected() {
    var sel = selected();
    if (!sel) return;
    var el = document.querySelector((view === "graph" ? "#dnodes .card" : "#rows .row") + '[data-id="' + sel + '"]');
    if (el) el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  /* -------------------------------------------------------------- 正文增强 */

  /* 代码块加"复制"按钮；正文渲染完调用一次。图片的灯箱走事件委托，不用逐个绑。 */
  function enhanceProse(root) {
    root.querySelectorAll("pre.code").forEach(function (pre) {
      if (pre.querySelector(".copy")) return;
      var b = document.createElement("button");
      b.className = "copy";
      b.type = "button";
      b.textContent = "复制";
      b.addEventListener("click", function () {
        navigator.clipboard.writeText(pre.querySelector("code").textContent).then(function () {
          b.textContent = "已复制";
          setTimeout(function () { b.textContent = "复制"; }, 1400);
        }, function () { toast("复制失败", true); });
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

  function renderDetail() {
    var el = $("#detail"), s = IDX[selected()];
    document.body.classList.toggle("editing", !!(editing && s));
    if (!s) {
      el.innerHTML = '<div class="placeholder">选一个步骤看详情。<br>'
        + '<span class="mono">↑ ↓</span> 移动 · <span class="mono">g</span> 切换视图 · '
        + '<span class="mono">n</span> 新建 · <span class="mono">e</span> 编辑 · <span class="mono">/</span> 搜索</div>';
      return;
    }
    if (editing) return renderEditor(s);

    var meta = ['<span class="pill s-' + s.status + '">' + s.status + "</span>"];
    if (s.date) meta.push(esc(s.date));
    if (s.commit) meta.push("commit " + esc(s.commit));
    if (s.author) meta.push(esc(s.author));
    if (s.parent) meta.push('parent <a href="#' + esc(s.parent) + '" data-goto="' + esc(s.parent) + '">' + esc(s.parent) + "</a>");
    if ((s.children || []).length) meta.push(s.children.length + " 个子步骤");
    (s.tags || []).forEach(function (t) { meta.push('<span class="tag">' + esc(t) + "</span>"); });

    var acts = "";
    if (canWrite()) {
      acts = '<div class="acts">'
        + '<button data-act="edit">✎ 编辑正文</button>'
        + ["wip", "done", "dead"].map(function (st) {
            return '<button data-status="' + st + '"' + (s.status === st ? ' class="on"' : "") + ">" + st + "</button>";
          }).join("")
        + '<span class="sp"></span>'
        + '<button data-act="child" class="primary">＋ 从这里派生</button>'
        + "</div>";
    }

    var body = window.md.render(s.body || "", { resolve: resolverFor(s) });

    var back = "";
    if ((s.backlinks || []).length) {
      back = '<div class="sec"><h3>被这些步骤引用</h3><div class="crumbs">'
        + s.backlinks.map(function (id) {
            return '<a href="#' + esc(id) + '" data-goto="' + esc(id) + '">' + esc(id) + "</a> "
              + esc((IDX[id] || {}).title || "");
          }).join("<br>")
        + "</div></div>";
    }

    var files = '<div class="sec"><h3>文件 · ' + (s.files || []).length + "</h3>";
    if ((s.files || []).length) {
      files += '<div class="files">' + s.files.map(function (f) {
        var url = fileURL(s, f.path);
        var thumb = IMG.test(f.path)
          ? '<img class="thumb zoomable" src="' + url + '" alt="' + esc(f.path) + '" loading="lazy">' : "";
        return '<div class="file">' + thumb + '<a href="' + url + '" target="_blank" rel="noopener">' + esc(f.path) + "</a>"
          + '<div class="sz">' + human(f.size) + (canWrite() ? ' · <a href="#" data-rm="' + esc(f.path) + '">删除</a>' : "") + "</div></div>";
      }).join("") + "</div>";
    } else {
      files += '<p class="dropnote">还没有附件。</p>';
    }
    if (canWrite()) files += '<p class="dropnote">把日志、脚本、图拖到本页任意位置即可上传，并自动在正文末尾插入引用。'
      + '想插在正文中间就进编辑模式，直接 Ctrl+V 粘贴截图。</p>';
    files += "</div>";

    el.innerHTML = crumbs(s) + '<h1 class="title">' + esc(s.title || "(无标题)") + "</h1>"
      + '<div class="meta">' + meta.join("") + "</div>" + acts
      + '<div class="prose">' + body + "</div>" + back + files;
    enhanceProse(el);
    el.scrollTop = 0;
  }

  /* -------------------------------------------------------------- 编辑器 */

  var TOOLS = [
    { k: "bold", html: "<b>B</b>", title: "粗体 (Ctrl+B)", wrap: ["**", "**"], ph: "粗体" },
    { k: "em", html: "<i>I</i>", title: "斜体 (Ctrl+I)", wrap: ["*", "*"], ph: "斜体" },
    { k: "code", html: "&lt;/&gt;", title: "行内代码", wrap: ["`", "`"], ph: "code" },
    { k: "h", html: "H", title: "小节标题", prefix: "## " },
    { k: "ul", html: "•", title: "无序列表", prefix: "- " },
    { k: "task", html: "☑", title: "任务列表", prefix: "- [ ] " },
    { k: "quote", html: "❞", title: "引用", prefix: "> " },
    { k: "pre", html: "{ }", title: "代码块", block: "```\n\n```", back: 4 },
    { k: "link", html: "🔗", title: "链接", wrap: ["[", "](url)"], ph: "文字" },
    { k: "img", html: "🖼", title: "插入图片（也可以直接 Ctrl+V 粘贴截图）" },
    { k: "table", html: "⊞", title: "插入表格（从 Excel 复制的内容直接粘贴也会自动转表格）" },
    { k: "hr", html: "—", title: "分隔线", block: "---" },
  ];

  function renderEditor(s) {
    $("#detail").innerHTML =
      '<div class="edhead">' + crumbs(s) + '<span class="sp"></span>'
      + '<button data-act="save" class="primary">保存 <kbd>Ctrl↵</kbd></button>'
      + '<button data-act="cancel">取消 <kbd>Esc</kbd></button></div>'
      + '<input class="title-input" id="ed-title" value="' + esc(s.title || "") + '" maxlength="200" placeholder="标题：一行说清这一步在干什么">'
      + '<div class="edtools">' + TOOLS.map(function (t) {
          return '<button type="button" data-md="' + t.k + '" title="' + esc(t.title) + '">' + t.html + "</button>";
        }).join("") + '<span class="sp"></span><span class="edhint mono" id="ed-status"></span></div>'
      + '<div class="edsplit">'
      + '<textarea class="editor" id="ed-body" spellcheck="false"></textarea>'
      + '<div class="prose edpreview" id="ed-preview"></div>'
      + "</div>"
      + '<p class="dropnote">截图 <b>Ctrl+V</b> 直接粘贴会自动上传并插入；从 Excel / 网页表格复制的内容粘贴会自动转成 markdown 表格；'
      + '文件也可以拖进编辑框。id 和 parent 不可改——只追加原则是溯源能成立的前提。</p>'
      + '<input type="file" id="ed-file" multiple accept="image/*,.log,.txt,.csv,.tsv,.json,.py,.sh,.yaml,.yml,.pdf" hidden>';

    var ta = $("#ed-body");
    ta.value = s.body || "";
    bindEditor(ta, s);
    updatePreview(s);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
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
    $("#ed-status").textContent = "上传中…";
    return files.reduce(function (chain, f) {
      return chain.then(function () {
        return uploadAuto(s, f).then(function (info) {
          if (IMG.test(info.path)) {
            insertBlock(ta, "![](" + info.path + ")", info.path.length + 3);  // 光标落在 ![|] 里，直接打图注
          } else {
            insertAt(ta, "[" + info.path + "](" + info.path + ")");
          }
          schedulePreview(s);
        });
      });
    }, Promise.resolve()).then(function () {
      $("#ed-status").textContent = "已插入 " + files.length + " 个文件";
      setTimeout(function () { var e = $("#ed-status"); if (e) e.textContent = ""; }, 2500);
    }).catch(function (e) { $("#ed-status").textContent = ""; fail(e); });
  }

  function bindEditor(ta, s) {
    ta.addEventListener("input", function () { schedulePreview(s); });

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
        toast("已转成 markdown 表格");
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
        var t = TOOLS.filter(function (x) { return x.k === b.getAttribute("data-md"); })[0];
        if (!t) return;
        if (t.k === "img") { $("#ed-file").click(); return; }
        if (t.k === "table") {
          insertBlock(ta, "| 列 1 | 列 2 | 列 3 |\n|---|---|---|\n|  |  |  |\n|  |  |  |");
        } else if (t.wrap) {
          wrapSel(ta, t.wrap[0], t.wrap[1], t.ph);
        } else if (t.prefix) {
          prefixLines(ta, t.prefix);
        } else if (t.block) {
          insertBlock(ta, t.block, t.back);
        }
        schedulePreview(s);
      });
    });
  }

  function saveEditor() {
    var s = IDX[selected()];
    if (!s) return;
    return patch(s.id, { title: $("#ed-title").value, body: $("#ed-body").value })
      .then(function () { editing = false; renderDetail(); refreshProjects(); toast("已保存"); })
      .catch(fail);
  }

  /* -------------------------------------------------------------- 写入 */

  function patch(id, body) {
    return papi("/steps/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function () { return refresh(); });
  }

  function openNew(parentId) {
    var p = IDX[parentId];
    $("#nf-parent").value = p ? p.id + "  " + p.title : "（无 — 新建一棵树的根）";
    $("#nf-parent").dataset.pid = p ? p.id : "";
    $("#nf-title").value = "";
    $("#nf-body").value = TEMPLATE;
    $("#nf-date").value = todayISO();
    $("#nf-status").value = "wip";
    $("#nf-commit").value = "";
    $("#dlg-new").showModal();
    setTimeout(function () { $("#nf-title").focus(); }, 30);
  }

  function submitNew() {
    var title = $("#nf-title").value.trim();
    if (!title) { toast("标题不能为空", true); return; }
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
      }),
    }).then(function (step) {
      return refresh().then(function () {
        select(step.id);
        scrollToSelected();
        refreshProjects();
        toast("已创建 " + step.id);
      });
    }).catch(fail);
  }

  /* -------------------------------------------------------------- 事件 */

  function onHash() { renderSelection(); renderDetail(); }

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

    var zi = e.target.closest("img.zoomable");
    if (zi) { e.preventDefault(); openLightbox(zi); return; }
    if (e.target.closest("#lightbox")) { closeLightbox(); return; }

    var goto = e.target.closest("[data-goto]");
    if (goto) { e.preventDefault(); select(goto.getAttribute("data-goto")); scrollToSelected(); return; }

    var hit = e.target.closest("#rows .row, #dnodes .card");
    if (hit) { select(hit.getAttribute("data-id")); return; }

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
      if (!s0 || !confirm("删除附件 " + path + "？（步骤本身不会被删除）")) return;
      papi("/steps/" + encodeURIComponent(s0.id) + "/files/" + path.split("/").map(encodeURIComponent).join("/"),
           { method: "DELETE" }).then(refresh).catch(fail);
      return;
    }

    var st = e.target.closest("[data-status]");
    if (st) { patch(selected(), { status: st.getAttribute("data-status") }).then(refreshProjects).catch(fail); return; }

    var act = e.target.closest("[data-act]");
    if (!act) return;
    var name = act.getAttribute("data-act");
    if (name === "edit") { editing = true; renderDetail(); }
    else if (name === "cancel") { editing = false; renderDetail(); }
    else if (name === "child") { openNew(selected()); }
    else if (name === "save") { saveEditor(); }
  });

  $("#proj").addEventListener("change", function (e) {
    location.href = e.target.value ? projectHref(e.target.value) : homeHref();
  });
  $("#search").addEventListener("input", function (e) { query = e.target.value; renderSelection(); });
  $("#btn-new").addEventListener("click", function () { openNew(selected()); });
  $("#btn-token").addEventListener("click", function () {
    var t = prompt("写入令牌（留空则清除）：", token());
    if (t !== null) { setToken(t.trim()); toast(t.trim() ? "令牌已保存到本浏览器" : "令牌已清除"); }
  });
  $("#btn-newproj").addEventListener("click", function () {
    var name = prompt("项目名：");
    if (!name || !name.trim()) return;
    api("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    }).then(function (p) { location.href = projectHref(p.slug); }).catch(fail);
  });
  $("#nf-ok").addEventListener("click", function (e) { e.preventDefault(); $("#dlg-new").close(); submitNew(); });
  window.addEventListener("hashchange", onHash);

  // Ctrl/⌘ + 滚轮缩放图视图
  $("#scroller").addEventListener("wheel", function (e) {
    if (!(e.ctrlKey || e.metaKey) || view !== "graph") return;
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });

  document.addEventListener("keydown", function (e) {
    if (!$("#lightbox").hidden && e.key === "Escape") { closeLightbox(); return; }
    var t = e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") {
      if (e.target.id === "ed-body" || e.target.id === "ed-title") {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveEditor(); return; }
        if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) {
          e.preventDefault(); wrapSel($("#ed-body"), "**", "**", "粗体"); schedulePreview(IDX[selected()]); return;
        }
        if ((e.metaKey || e.ctrlKey) && (e.key === "i" || e.key === "I")) {
          e.preventDefault(); wrapSel($("#ed-body"), "*", "*", "斜体"); schedulePreview(IDX[selected()]); return;
        }
        if (e.key === "Escape") { e.preventDefault(); editing = false; renderDetail(); return; }
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
      view = view === "graph" ? "list" : "graph";
      localStorage.setItem("trace.view", view);
      applyView(); renderSelection(); scrollToSelected();
    } else if (e.key === "n" && canWrite()) { e.preventDefault(); openNew(selected()); }
    else if (e.key === "e" && canWrite() && selected()) { e.preventDefault(); editing = true; renderDetail(); }
    else if (e.key === "Escape") { if (editing) { editing = false; renderDetail(); } else select(""); }
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
      if (!s) { toast("先选一个步骤", true); return; }
      var files = Array.prototype.slice.call(e.dataTransfer.files || []);
      if (!files.length) return;
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
        .then(function () { toast("已上传 " + files.length + " 个文件并写入正文"); })
        .catch(fail);
    });
  }

  /* -------------------------------------------------------------- 启动 */

  paintToken();
  if (MODE === "static" || !PROJECT) {
    $("#btn-new").hidden = MODE === "static" || !PROJECT;
    $("#btn-token").hidden = MODE === "static";
  }
  if (MODE === "static") {
    $("#btn-newproj").hidden = true;
    $("#live").className = "dot";
    $("#live").title = "静态导出 — 只读";
  }
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
        refreshProjects();
        if (PROJECT && !editing) refresh();   // 正在编辑时不要把用户的输入冲掉
      }
    };
  }
})();

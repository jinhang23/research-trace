/* md.js — 极简 markdown 渲染器（零依赖，不走 CDN，file:// 下可用）。
 *
 * 覆盖科研笔记实际会用到的语法：标题、围栏代码、行内代码、列表、任务列表、
 * 引用、表格（含列对齐 + 数字列自动右对齐）、分隔线、粗体斜体删除线、链接、
 * 裸链接自动识别、图片（独占一段时渲染成带图注的 figure）、[[007]] 内部跳转。
 *
 * 安全：先整体转义 HTML，之后才插入自己生成的标签。正文里写不进裸 HTML。
 */
(function (global) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  var NUL = String.fromCharCode(0); // 转义后的文本里不可能出现，用作行内代码的占位符边界

  function safeHref(h, resolve) {
    h = String(h || "").trim();
    if (/^\s*(javascript|data|vbscript):/i.test(h)) return "#";
    if (/^([a-z][a-z0-9+.-]*:|\/\/|\/|#)/i.test(h)) return h;   // 绝对 / 协议 / 锚点：原样
    return resolve ? resolve(h) : h;                              // 相对路径：交给调用方重写
  }

  function imgTag(alt, src, resolve, cls, title) {
    return '<img class="' + (cls || "") + '" alt="' + alt + '" src="' + safeHref(src, resolve) + '"'
      + (title ? ' title="' + title + '"' : "") + ' loading="lazy">';
  }

  // 标题语法 ![alt](src "说明") 里的引号在整体转义之后已经变成 &quot;，
  // 正则必须按转义后的样子写——按 `"` 写会导致整个图片语法匹配不上。
  var TITLE = "(?:\\s+&quot;(.*?)&quot;)?";
  var RE_IMG = new RegExp("!\\[([^\\]]*)\\]\\(([^)\\s]+)" + TITLE + "\\)", "g");
  var RE_LINK = new RegExp("\\[([^\\]]+)\\]\\(([^)\\s]+)" + TITLE + "\\)", "g");
  var RE_LONE_IMG = new RegExp("^!\\[([^\\]]*)\\]\\(([^)\\s]+)" + TITLE + "\\)$");

  function inline(text, resolve) {
    var codes = [];
    text = text.replace(/`([^`]+)`/g, function (_, c) {
      codes.push(c);
      return NUL + (codes.length - 1) + NUL;
    });

    // 裸链接。必须在 [text](url) 之前处理，且前导字符里排除 ( 和 [，
    // 否则 [标题](http://x) 里的 url 会被再包一层。
    text = text.replace(/(^|[\s、，。；：])(https?:\/\/[^\s<>"'()（）、，。；：]+)/g,
      function (_, pre, url) { return pre + "[" + url + "](" + url + ")"; });

    text = text.replace(RE_IMG, function (_, alt, src, title) {
      return imgTag(alt, src, resolve, "inline-img zoomable", title);
    });
    text = text.replace(RE_LINK, function (_, t, h, title) {
      var href = safeHref(h, resolve);
      var ext = /^[a-z][a-z0-9+.-]*:/i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
      return '<a href="' + href + '"' + ext + (title ? ' title="' + title + '"' : "") + ">" + t + "</a>";
    });
    // [[007]] —— 森林结构下表达"另见某条支"的软链接；反向链接由后端算出
    text = text.replace(/\[\[\s*(\d+[a-z]*)\s*\]\]/g, function (_, id) {
      return '<a class="wikilink" href="#' + id + '" data-goto="' + id + '">' + id + "</a>";
    });

    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    return text.replace(new RegExp(NUL + "(\\d+)" + NUL, "g"), function (_, i) {
      return "<code>" + codes[+i] + "</code>";
    });
  }

  function cells(line) {
    return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(function (c) { return c.trim(); });
  }

  /* 数字单元格：剥掉强调/空白之后是纯数值（允许千分位、百分号、科学计数、± 前缀、占位横线）。 */
  var NUMERIC = /^[-+±−]?[\d,]*\.?\d+(?:[eE][-+]?\d+)?\s*%?$/;
  function isNumeric(c) {
    var v = c.replace(/[*_`\s]|&nbsp;/g, "");
    if (!v || v === "-" || v === "—" || v === "/") return null;   // 空位，不参与判断
    return NUMERIC.test(v);
  }

  /* 表格列对齐：显式的 :--- / ---: / :---: 优先；没写就把"整列都是数字"的列右对齐。
     科研表格里的指标列右对齐之后小数点自然成列，好读得多。 */
  function columnAligns(sepLine, head, rows) {
    var explicit = cells(sepLine).map(function (c) {
      var l = /^:/.test(c), r = /:$/.test(c);
      return l && r ? "center" : r ? "right" : l ? "left" : "";
    });
    return head.map(function (_, i) {
      if (explicit[i]) return explicit[i];
      var seen = 0;
      for (var k = 0; k < rows.length; k++) {
        var v = isNumeric(rows[k][i] == null ? "" : rows[k][i]);
        if (v === false) return "";
        if (v === true) seen++;
      }
      return seen ? "right" : "";
    });
  }

  var BLOCK_START = /^(\s*(#{1,6}\s|&gt;|```|~~~|\|)|\s*([-*+]|\d+[.)])\s|\s*(-{3,}|\*{3,}|_{3,})\s*$)/;

  /* 输入必须是**已经转义**的行数组。render() 负责转义，blockquote 递归时直接复用。 */
  function renderLines(lines, opts) {
    var resolve = opts && opts.resolve;
    var out = [];
    var i = 0;
    function isBlank(s) { return /^\s*$/.test(s); }

    while (i < lines.length) {
      var line = lines[i];
      if (isBlank(line)) { i++; continue; }

      var fence = line.match(/^\s*(```+|~~~+)\s*([\w.+-]*)\s*$/);
      if (fence) {
        var mark = fence[1][0], lang = fence[2], buf = [];
        var closer = new RegExp("^\\s*\\" + mark + "{3,}\\s*$");
        i++;
        while (i < lines.length && !closer.test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push('<pre class="code"' + (lang ? ' data-lang="' + lang + '"' : "") + "><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }

      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { var lv = h[1].length; out.push("<h" + lv + ">" + inline(h[2], resolve) + "</h" + lv + ">"); i++; continue; }

      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

      if (/^\s*&gt;\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*&gt;\s?/, ""));
        out.push("<blockquote>" + renderLines(q, opts) + "</blockquote>");
        continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        var head = cells(line), sep = lines[i + 1];
        i += 2;
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        var al = columnAligns(sep, head, rows);
        var at = function (k) { return al[k] ? ' class="ta-' + al[k] + '"' : ""; };
        out.push(
          '<div class="tablewrap"><table><thead><tr>' +
          head.map(function (c, k) { return "<th" + at(k) + ">" + inline(c, resolve) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + head.map(function (_, k) {
              return "<td" + at(k) + ">" + inline(r[k] == null ? "" : r[k], resolve) + "</td>";
            }).join("") + "</tr>";
          }).join("") +
          "</tbody></table></div>"
        );
        continue;
      }

      var li = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (li) {
        var tag = /\d/.test(li[2]) ? "ol" : "ul";
        var baseIndent = li[1].length;
        var items = [];
        var isTask = false;
        var item = function (raw) {
          var t = raw.match(/^\[([ xX])\]\s+(.*)$/);
          if (!t) return inline(raw, resolve);
          isTask = true;
          return '<label class="task"><input type="checkbox" disabled'
            + (t[1] === " " ? "" : " checked") + ">" + inline(t[2], resolve) + "</label>";
        };
        while (i < lines.length) {
          var m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (m && m[1].length <= baseIndent) {
            items.push([item(m[3])]);
            i++;
          } else if (m && items.length) {           // 缩进更深 → 一层子列表
            var subIndent = m[1].length, sub = [];
            while (i < lines.length) {
              var n = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
              if (!n || n[1].length < subIndent) break;
              sub.push("<li>" + item(n[3]) + "</li>");
              i++;
            }
            items[items.length - 1].push("<ul>" + sub.join("") + "</ul>");
          } else if (items.length && !isBlank(lines[i]) && !BLOCK_START.test(lines[i])) {
            items[items.length - 1][0] += " " + inline(lines[i].trim(), resolve);  // 续行
            i++;
          } else break;
        }
        out.push("<" + tag + (isTask ? ' class="tasks"' : "") + ">"
          + items.map(function (p) { return "<li>" + p.join("") + "</li>"; }).join("")
          + "</" + tag + ">");
        continue;
      }

      // 独占一段的图片渲染成 figure：图注用 "标题" 里的文字，没有就用 alt。
      // 科研笔记里的图基本都需要一句说明，否则半年后看不出画的是什么。
      var lone = line.trim().match(RE_LONE_IMG);
      if (lone && (i + 1 >= lines.length || isBlank(lines[i + 1]))) {
        var cap = lone[3] || lone[1];
        out.push('<figure>' + imgTag(lone[1], lone[2], resolve, "zoomable")
          + (cap ? "<figcaption>" + inline(cap, resolve) + "</figcaption>" : "") + "</figure>");
        i++;
        continue;
      }

      var para = [];
      while (i < lines.length && !isBlank(lines[i]) && !(para.length && BLOCK_START.test(lines[i]))) {
        para.push(lines[i++]);
      }
      out.push("<p>" + inline(para.join("\n"), resolve).replace(/\n/g, "<br>") + "</p>");
    }
    return out.join("\n");
  }

  function render(src, opts) {
    return renderLines(esc(String(src || "").replace(/\r\n?/g, "\n")).split("\n"), opts || {});
  }

  global.md = { render: render, esc: esc };
})(typeof globalThis !== "undefined" ? globalThis : this);

/* md.js — 极简 markdown 渲染器（零依赖，不走 CDN，file:// 下可用）。
 *
 * 只覆盖科研笔记实际会用到的语法：标题、围栏代码、行内代码、列表、引用、
 * 表格、分隔线、粗体斜体删除线、链接、图片、[[007]] 内部跳转。
 *
 * 安全：先整体转义 HTML，之后才插入自己生成的标签。正文里写不进裸 HTML。
 */
(function (global) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  var NUL = String.fromCharCode(0); // 转义后的文本里不可能出现，用作占位符边界

  function safeHref(h, resolve) {
    h = String(h || "").trim();
    if (/^\s*(javascript|data|vbscript):/i.test(h)) return "#";
    if (/^([a-z][a-z0-9+.-]*:|\/\/|\/|#)/i.test(h)) return h;   // 绝对 / 协议 / 锚点：原样
    return resolve ? resolve(h) : h;                              // 相对路径：交给调用方重写
  }

  function inline(text, resolve) {
    var codes = [];
    text = text.replace(/`([^`]+)`/g, function (_, c) {
      codes.push(c);
      return NUL + (codes.length - 1) + NUL;
    });

    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, function (_, alt, src) {
      return '<img alt="' + alt + '" src="' + safeHref(src, resolve) + '" loading="lazy">';
    });
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, function (_, t, h) {
      var href = safeHref(h, resolve);
      var ext = /^[a-z][a-z0-9+.-]*:/i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
      return '<a href="' + href + '"' + ext + ">" + t + "</a>";
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
        var head = cells(line);
        i += 2;
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        out.push(
          '<div class="tablewrap"><table><thead><tr>' +
          head.map(function (c) { return "<th>" + inline(c, resolve) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + r.map(function (c) { return "<td>" + inline(c, resolve) + "</td>"; }).join("") + "</tr>";
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
        while (i < lines.length) {
          var m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (m && m[1].length <= baseIndent) {
            items.push([inline(m[3], resolve)]);
            i++;
          } else if (m && items.length) {           // 缩进更深 → 一层子列表
            var subIndent = m[1].length, sub = [];
            while (i < lines.length) {
              var n = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
              if (!n || n[1].length < subIndent) break;
              sub.push("<li>" + inline(n[3], resolve) + "</li>");
              i++;
            }
            items[items.length - 1].push("<ul>" + sub.join("") + "</ul>");
          } else if (items.length && !isBlank(lines[i]) && !BLOCK_START.test(lines[i])) {
            items[items.length - 1][0] += " " + inline(lines[i].trim(), resolve);  // 续行
            i++;
          } else break;
        }
        out.push("<" + tag + ">" + items.map(function (p) { return "<li>" + p.join("") + "</li>"; }).join("") + "</" + tag + ">");
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
})(window);

/* md.js — 极简 markdown 渲染器（零依赖，不走 CDN，file:// 下可用）。
 *
 * 覆盖科研笔记实际会用到的语法：标题、围栏代码、行内代码、列表（任意层嵌套、
 * 有序/无序/任务、项内可放代码块和引用）、引用、表格（含列对齐 + 数值列自动
 * 右对齐；`\|` 按 CommonMark 当字面竖线）、分隔线、粗体斜体删除线（* 和 _ 两套）、
 * 链接与图片（含 <尖括号> 目标和括号成对的裸路径）、裸链接自动识别、
 * 数学公式原样保留、[[007]] 内部跳转。
 *
 * 有意不做：引用式链接 [a][ref]、脚注、setext 标题、四空格缩进代码块。
 * 这些在实验记录里几乎不出现，做进来只会让下面这堆正则更难被人看懂。
 *
 * 安全：先整体转义 HTML，之后才插入自己生成的标签。正文里写不进裸 HTML。
 * 正文是人和 agent 通过 API 写进来的不可信输入，所以链接目标还要过 safeHref。
 */
(function (global) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  /* ------------------------------------------------------------ 链接目标消毒 */

  var NAMED_ENT = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", colon: ":",
    tab: "\t", newline: "\n", sol: "/", nbsp: " ", semi: ";"
  };
  function fromCp(n) { return (n >= 0 && n <= 0x10ffff) ? String.fromCodePoint(n) : ""; }

  /* 只用来生成「拿去比对黑名单」的探针，不用来生成真正输出的 href。
     浏览器会在属性值上解码实体，所以 &#106;avascript: / java&Tab;script: 这类写法
     到了浏览器手里就是 javascript:；黑名单必须看解码之后的样子。
     反复解码几轮是为了挡 &amp;#106; 这种套娃——宁可误判成 # 也不要漏。 */
  function decodeEntities(s) {
    for (var round = 0; round < 3; round++) {
      var next = s
        .replace(/&#x([0-9a-f]+);?/gi, function (_, hex) { return fromCp(parseInt(hex, 16)); })
        .replace(/&#(\d+);?/g, function (_, dec) { return fromCp(parseInt(dec, 10)); })
        .replace(/&([a-z][a-z0-9]*);?/gi, function (m, name) {
          var k = name.toLowerCase();
          return Object.prototype.hasOwnProperty.call(NAMED_ENT, k) ? NAMED_ENT[k] : m;
        });
      if (next === s) break;
      s = next;
    }
    return s;
  }

  var BAD_SCHEME = /^(?:javascript|data|vbscript|livescript|mocha):/i;

  /* WHATWG URL 解析在认协议之前，会先剥掉首尾的 C0 控制字符与空格、
     并删掉中间所有的 tab/换行。所以 "\x01javascript:alert(1)" 在浏览器眼里
     就是一个 javascript: URL —— 只用 trim() 加一个 \s 前缀的正则判断，会整条漏过去。
     这里的做法是：先按同一套规则把控制字符清干净，再判断，并且**返回清干净的那份**
     （返回原串等于把控制字符留给浏览器自己去剥，等于没修）。 */
  function safeHref(h, resolve) {
    var s = String(h == null ? "" : h).replace(/[\u0000-\u001f\u007f]/g, "").trim();
    var probe = decodeEntities(s).replace(/[\u0000-\u0020\u007f]/g, "");
    if (BAD_SCHEME.test(probe)) return "#";
    if (/^([a-z][a-z0-9+.-]*:|\/\/|\/|#)/i.test(s)) return s;   // 绝对 / 协议 / 锚点：原样
    return resolve ? resolve(s) : s;                             // 相对路径：交给调用方重写
  }

  function imgTag(alt, src, resolve, cls, title) {
    return '<img class="' + (cls || "") + '" alt="' + alt + '" src="' + safeHref(src, resolve) + '"'
      + (title ? ' title="' + title + '"' : "") + ' loading="lazy">';
  }

  /* 目标地址两种写法：CommonMark 的 <...> 尖括号形式（里面允许空格），
     以及圆括号成对的裸路径。`loss curve (run 42).png` 这种附件名在实验记录里太常见，
     只认「不含空格和括号」的老写法会让这类图/附件在网页上根本不出现。
     标题（图注）里的引号在整体转义之后已经是 &quot; / &#39;，
     正则必须按转义后的样子写——按 `"` 写会导致整个图片语法匹配不上。
     标题允许跨行：FORMAT.md §5 自己给的示例就是把长图注折成两行写的。 */
  var DEST = "(?:&lt;([^\\n]*?)&gt;|((?:[^()\\s]|\\((?:[^()\\s]|\\([^()\\s]*\\))*\\))+))";
  var TITLE = "(?:\\s+(?:&quot;([\\s\\S]*?)&quot;|&#39;([\\s\\S]*?)&#39;))?";
  var IMG_SRC = "!\\[([^\\]]*)\\]\\(\\s*" + DEST + TITLE + "\\s*\\)";
  var LINK_SRC = "\\[([^\\]]+)\\]\\(\\s*" + DEST + TITLE + "\\s*\\)";
  var RE_IMG = new RegExp(IMG_SRC, "g");
  var RE_LINK = new RegExp(LINK_SRC, "g");
  var RE_LONE_IMG = new RegExp("^" + IMG_SRC + "$");
  // 捕获组：1=alt/文字 2=<尖括号>目标 3=裸目标 4="标题" 5='标题'
  function destOf(m) { return m[2] !== undefined ? m[2] : (m[3] || ""); }
  function titleOf(m) { return m[4] !== undefined ? m[4] : (m[5] !== undefined ? m[5] : ""); }

  /* ---------------------------------------------------------------- 行内 */

  var SENT = "\u0000";   // render() 已把正文里的控制字符清掉，所以它一定不会和正文撞

  // CommonMark 的反斜杠转义只对 ASCII 标点生效。这条限制很重要：
  // 实验记录里全是 C:\Users\... 这类 Windows 路径，若无差别地吃掉反斜杠，
  // 路径会当场变形；按标准只在标点前生效，路径原样保留。
  var ESCAPABLE = "!\"#$%&'()*+,\\-./:;<=>?@\\[\\\\\\]^_`{|}~";

  function inline(text, resolve, brk) {
    var holes = [];
    function hole(html) { holes.push(html); return SENT + (holes.length - 1) + SENT; }

    // 行内代码。先抠出来，后面的强调/链接规则就碰不到它了。
    text = text.replace(/`([^`]+)`/g, function (_, c) { return hole("<code>" + c + "</code>"); });

    // 数学公式。不引 KaTeX（零依赖是硬约束），但**必须原样留住**：
    // 公式一旦被 * _ \ 那几条规则啃过，人和 LLM 都再也读不回原式。
    // 所以整段抠成不可变文本、连 $ 一起保留，将来接排版器时纯属加成。
    text = text.replace(/\$\$([^\n]+?)\$\$/g, function (m) { return hole('<span class="math">' + m + "</span>"); });
    // 单个 $ 的判定按 pandoc 那套：$ 后不能是空白、$ 前不能是空白、收尾的 $ 后面不能跟数字。
    // 这条「后面不能跟数字」正是用来放过「花了 $5 又花了 $3」这种货币写法的。
    text = text.replace(/(^|[^\\$])\$(?!\s)((?:[^$\n])*?[^\s\\])\$(?!\d)/g,
      function (_, pre, body) { return pre + hole('<span class="math">$' + body + "$</span>"); });

    // 反斜杠转义：\| \* \_ \[ … 一律变回字面字符，且不再参与后面的语法。
    text = text.replace(new RegExp("\\\\([" + ESCAPABLE + "])", "g"), function (_, c) { return hole(c); });

    // 裸链接。必须在 [text](url) 之前处理，且前导字符里排除 ( 和 [，
    // 否则 [标题](http://x) 里的 url 会被再包一层。
    // 括号按成对计入：维基百科那种 .../Foo_(bar) 链接不这样做会被截成坏链接。
    text = text.replace(/(^|[\s、，。；：])(https?:\/\/(?:[^\s<>"'()（）、，。；：]|\((?:[^\s()]|\([^\s()]*\))*\))+)/g,
      function (_, pre, url) { return pre + "[" + url + "](" + url + ")"; });

    text = text.replace(RE_IMG, function () {
      var m = arguments;
      return hole(imgTag(m[1], destOf(m), resolve, "inline-img zoomable", titleOf(m)));
    });
    // 链接：开合标签各自入洞，中间的文字留在外面继续吃强调规则。
    // （旧实现是整条 <a …> 生成完再跑强调替换，于是 [x](https://a/**b**/c) 的
    //  href 里会被塞进 <strong>，链接指向一个不存在的地址。）
    text = text.replace(RE_LINK, function () {
      var m = arguments;
      var href = safeHref(destOf(m), resolve);
      var ext = /^[a-z][a-z0-9+.-]*:/i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
      var title = titleOf(m);
      return hole('<a href="' + href + '"' + ext + (title ? ' title="' + title + '"' : "") + ">")
        + m[1] + hole("</a>");
    });
    // <https://…> 自动链接。放在上面两条之后，免得抢走 ![](<a b.png>) 里的尖括号目标。
    text = text.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, function (_, url) {
      var href = safeHref(url, resolve);
      return hole('<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + url + "</a>");
    });
    // [[007]] —— 森林结构下表达"另见某条支"的软链接；反向链接由后端算出
    text = text.replace(/\[\[\s*(\d+[a-z]*)\s*\]\]/g, function (_, id) {
      return hole('<a class="wikilink" href="#' + id + '" data-goto="' + id + '">' + id + "</a>");
    });

    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^\w_])__([^_]+?)__(?![\w])/g, "$1<strong>$2</strong>");
    text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, "$1<em>$2</em>");
    // 下划线强调的前后都要求不是词内字符，否则 some_var_name 会被劈成斜体
    text = text.replace(/(^|[^\w_])_([^_\n]+?)_(?![\w])/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    if (brk) text = text.replace(/\n/g, "<br>");

    // 洞里可能还嵌着洞（比如 ![`a`](x) 的 alt），所以要循环填到没有为止。
    var guard = 8;
    while (text.indexOf(SENT) >= 0 && guard-- > 0) {
      text = text.replace(new RegExp(SENT + "(\\d+)" + SENT, "g"), function (_, i) {
        var v = holes[+i];
        return v === undefined ? "" : v;
      });
    }
    return text;
  }

  /* ---------------------------------------------------------------- 表格 */

  /* 按 CommonMark 切分表格行：`\|` 是**字面竖线**，不是分隔符。
     从 Excel / Google Sheets 粘表格时会自动产出这种转义（含管道命令、`低|中|高`
     这类取值都会），认不出来就会把一格劈成两格、把最右边的列整个挤掉。 */
  function cells(line) {
    var s = line.replace(/^\s*\|/, "");
    var out = [], cur = "";
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (ch === "\\" && s.charAt(i + 1) === "|") { cur += "\\|"; i++; continue; }
      if (ch === "|") { out.push(cur); cur = ""; continue; }
      cur += ch;
    }
    out.push(cur);
    // 行尾那根收口的竖线会切出一个空格子，去掉它；以 `\|` 结尾时切不出空格子，不受影响
    if (out.length > 1 && /^\s*$/.test(out[out.length - 1])) out.pop();
    return out.map(function (c) { return c.trim(); });
  }

  /* 数值单元格：剥掉强调/空白之后是一个数，可带正负号、千分位、百分号、科学计数，
     并且允许「主值 ± 误差」这种写法。
     FORMAT.md §4 明写「有方差就写进去（0.943 ± 0.004）」，
     所以带 ± 的格子必须仍然算数值列——否则照着格式标准写，反而丢掉标准承诺的
     右对齐和底纹条，标准和实现就打架了。
     带单位的（40 s）故意**不**算数值：FORMAT.md 说这是对的，单位列当文字列读更清楚。 */
  var NUM = "[+\\-\u2212\u00b1\u2213]?(?:\\d[\\d,]*(?:\\.\\d+)?|\\.\\d+)(?:[eE][+\\-\u2212]?\\d+)?%?";
  var PM = "(?:\u00b1|\u2213|\\+\\/-|\\+-)";                       // ± ∓ +/- +-
  var NUMERIC = new RegExp("^" + NUM + "(?:" + PM + NUM + ")?$");
  var NUM_HEAD = new RegExp("^" + NUM);

  function bare(c) { return String(c == null ? "" : c).replace(/[*_`\s]|&nbsp;/g, ""); }

  /* true=是数值，false=不是，null=空位（`-` `—` `/` 这类占位，不参与整列判断） */
  function isNumeric(c) {
    var v = bare(c);
    if (!v || v === "-" || v === "\u2014" || v === "\u2013" || v === "/") return null;
    return NUMERIC.test(v);
  }

  /* 取出「主数值」——底纹条按它算长度，误差项不参与。
     纯前缀的 ± （`±0.5`）按量值取正：那种写法表达的是幅度，不是负数。 */
  function numericValue(c) {
    if (isNumeric(c) !== true) return NaN;
    var m = bare(c).match(NUM_HEAD);
    if (!m) return NaN;
    var v = m[0].replace(/,/g, "").replace(/%$/, "")
      .replace(/^[\u00b1\u2213]/, "").replace(/\u2212/g, "-");
    return parseFloat(v);
  }

  /* 表格列：显式的 :--- / ---: / :---: 优先；没写就把"整列都是数字"的列右对齐。
     科研表格里的指标列右对齐之后小数点自然成列，好读得多。
     numeric[] 单独算一份（不受显式对齐影响），供底纹条使用。 */
  function columnInfo(sepLine, head, rows) {
    var explicit = cells(sepLine).map(function (c) {
      var l = /^:/.test(c), r = /:$/.test(c);
      return l && r ? "center" : r ? "right" : l ? "left" : "";
    });
    var align = [], numeric = [];
    head.forEach(function (_, i) {
      var seen = 0, ok = true;
      for (var k = 0; k < rows.length; k++) {
        var v = isNumeric(rows[k][i]);
        if (v === false) { ok = false; break; }
        if (v === true) seen++;
      }
      numeric[i] = ok && seen > 0;
      align[i] = explicit[i] || (numeric[i] ? "right" : "");
    });
    return { align: align, numeric: numeric };
  }

  /* ---------------------------------------------------------------- 块 */

  var BLOCK_START = /^(\s*(#{1,6}\s|&gt;|```|~~~|\||\$\$)|\s*([-*+]|\d+[.)])\s|\s*(-{3,}|\*{3,}|_{3,})\s*$)/;
  var LI_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(.*)$/;

  function isBlank(s) { return /^\s*$/.test(s); }
  function indentOf(s) { return s.match(/^\s*/)[0].length; }

  /* 列表块：把每一项的行收集起来、剥掉这一项的缩进，然后**递归**丢回 renderLines。
     子列表、项内的围栏代码、项内的引用块因此全部自然支持，不必在这里为每种嵌套
     各写一遍分支。旧实现是手工展开了一层子列表，于是三层缩进被压平、
     有序子列表退化成 <ul> 丢掉编号、项内的代码块把列表劈成两段还泄漏出缩进。 */
  function listBlock(lines, i0, opts) {
    var m0 = lines[i0].match(LI_RE);
    var base = m0[1].length;
    var ordered = /\d/.test(m0[2]);
    var startNo = ordered ? parseInt(m0[2], 10) : 1;
    var i = i0, items = [], isTask = false;

    while (i < lines.length) {
      if (isBlank(lines[i])) {
        // 松散列表：项与项之间的空行只是段间距，不该把一个列表切成两个 <ul>
        var k = i;
        while (k < lines.length && isBlank(lines[k])) k++;
        var nx = k < lines.length ? lines[k].match(LI_RE) : null;
        if (!nx || nx[1].length > base || /\d/.test(nx[2]) !== ordered) break;
        i = k;
      }
      var m = lines[i].match(LI_RE);
      if (!m || m[1].length > base || /\d/.test(m[2]) !== ordered) break;

      var pad = m[1].length + m[2].length + m[3].length;   // 这一项的内容起始列
      var buf = [m[4]];
      i++;
      while (i < lines.length) {
        var ln = lines[i];
        if (isBlank(ln)) {
          var j = i;
          while (j < lines.length && isBlank(lines[j])) j++;
          if (j >= lines.length || indentOf(lines[j]) < base + 1) break;   // 空行后不再缩进 → 本项结束
          while (i < j) { buf.push(""); i++; }
          continue;
        }
        if (indentOf(ln) >= base + 1) { buf.push(ln.slice(Math.min(pad, indentOf(ln)))); i++; continue; }
        if (LI_RE.test(ln)) break;            // 同级或更浅的新项
        if (BLOCK_START.test(ln)) break;      // 另起一个块
        buf.push(ln.trim());                  // 懒续行
        i++;
      }

      var t = buf[0].match(/^\[([ xX])\]\s+(.*)$/);
      var checked = false;
      if (t) { isTask = true; checked = t[1] !== " "; buf[0] = t[2]; }
      var html = renderLines(buf, opts).replace(/^<p>([\s\S]*?)<\/p>/, "$1");   // 紧凑列表：首段不套 <p>
      if (t) {
        // <label> 只包住这一项自己的首段。项里还挂着子列表时不能一起包进去，
        // 否则子项的勾选框会落在父项的 label 内，点父项等于点子项。
        var cut = html.search(/\n(?=<)/);
        var lead = cut < 0 ? html : html.slice(0, cut);
        html = '<label class="task"><input type="checkbox" disabled'
          + (checked ? " checked" : "") + ">" + lead + "</label>" + (cut < 0 ? "" : html.slice(cut));
      }
      items.push(html);
    }

    var tag = ordered ? "ol" : "ul";
    var attr = (isTask ? ' class="tasks"' : "") + (ordered && startNo !== 1 ? ' start="' + startNo + '"' : "");
    return {
      next: i,
      html: "<" + tag + attr + ">"
        + items.map(function (h) { return "<li>" + h + "</li>"; }).join("")
        + "</" + tag + ">"
    };
  }

  /* 输入必须是**已经转义**的行数组。render() 负责转义，blockquote / 列表项递归时直接复用。 */
  function renderLines(lines, opts) {
    var resolve = opts && opts.resolve;
    var out = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];
      if (isBlank(line)) { i++; continue; }

      // 围栏代码：info string 可以带参数（```python title=x），闭合只认光秃秃的一行围栏。
      // 内容按围栏自身的缩进对齐剥掉，免得列表项里的代码块带着多余空格显示。
      var fence = line.match(/^(\s*)(`{3,}|~{3,})\s*(\S*)([^`]*)$/);
      if (fence) {
        var indent = fence[1].length, mark = fence[2][0], lang = fence[3], buf = [];
        var closer = new RegExp("^\\s*\\" + mark + "{3,}\\s*$");
        i++;
        while (i < lines.length && !closer.test(lines[i])) {
          buf.push(lines[i].slice(Math.min(indent, indentOf(lines[i]))));
          i++;
        }
        i++;
        out.push('<pre class="code"' + (lang ? ' data-lang="' + lang + '"' : "") + "><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }

      // $$ 独占几行的行间公式：原样保留（含定界符），只用 <pre> 把换行留住。
      // 不解析、不转写——渲染器认不出的公式，至少要保证人和 LLM 读到的还是原式。
      if (/^\s*\$\$\s*$/.test(line)) {
        var mbuf = [];
        i++;
        while (i < lines.length && !/^\s*\$\$\s*$/.test(lines[i])) mbuf.push(lines[i++]);
        i++;
        out.push('<pre class="math math-block">$$\n' + mbuf.join("\n") + "\n$$</pre>");
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
        var info = columnInfo(sep, head, rows);
        var at = function (k) { return info.align[k] ? ' class="ta-' + info.align[k] + '"' : ""; };
        // data-num 是给底纹条用的「主数值」（0.943 ± 0.004 → 0.943）。
        // 它是从单元格文本现算的派生量，不是新语法：去掉这层属性一个字都不丢。
        var num = function (k, v) {
          if (!info.numeric[k]) return "";
          var n = numericValue(v);
          return isFinite(n) ? ' data-num="' + n + '"' : "";
        };
        out.push(
          '<div class="tablewrap"><table><thead><tr>' +
          head.map(function (c, k) { return "<th" + at(k) + ">" + inline(c, resolve) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + head.map(function (_, k) {
              var v = r[k] == null ? "" : r[k];
              return "<td" + at(k) + num(k, v) + ">" + inline(v, resolve) + "</td>";
            }).join("") + "</tr>";
          }).join("") +
          "</tbody></table></div>"
        );
        continue;
      }

      if (LI_RE.test(line)) {
        var lb = listBlock(lines, i, opts);
        out.push(lb.html);
        i = lb.next;
        continue;
      }

      // 独占一段的图片渲染成 figure：图注用 "标题" 里的文字，没有就用 alt。
      // 科研笔记里的图基本都需要一句说明，否则半年后看不出画的是什么。
      // 整段一起匹配（不是只看一行）是因为 FORMAT.md §5 的示例把长图注折行写了。
      if (/^\s*!\[/.test(line)) {
        var j2 = i, chunk = [];
        while (j2 < lines.length && !isBlank(lines[j2])) chunk.push(lines[j2++]);
        var lone = chunk.join("\n").trim().match(RE_LONE_IMG);
        if (lone) {
          var cap = titleOf(lone) || lone[1];
          out.push("<figure>" + imgTag(lone[1], destOf(lone), resolve, "zoomable")
            + (cap ? "<figcaption>" + inline(cap, resolve) + "</figcaption>" : "") + "</figure>");
          i = j2;
          continue;
        }
      }

      var para = [];
      while (i < lines.length && !isBlank(lines[i]) && !(para.length && BLOCK_START.test(lines[i]))) {
        para.push(lines[i++]);
      }
      out.push("<p>" + inline(para.join("\n"), resolve, true) + "</p>");
    }
    return out.join("\n");
  }

  function render(src, opts) {
    var s = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
    // 控制字符一律先剥掉，两个理由：
    // (1) 行内代码/链接用 \u0000 当占位哨兵，正文里混进真的 \u0000 会串到别的 code span 上；
    // (2) 浏览器解析 URL 前会剥掉首尾的 C0 控制字符，"\x01javascript:" 在 href 里照样执行。
    // 它们本来也不是人能读的字符，去掉不损失任何正文信息（note.md 里的原字节不受影响）。
    s = s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
    return renderLines(esc(s).split("\n"), opts || {});
  }

  global.md = {
    render: render,
    esc: esc,
    safeHref: safeHref,
    isNumeric: isNumeric,
    numericValue: numericValue
  };
})(typeof globalThis !== "undefined" ? globalThis : this);

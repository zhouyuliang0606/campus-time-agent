/**
 * 全站唯一一份 HTML 转义。2026-10-08 收口 XSS 时立的规矩，别在这儿加"第二种写法"。
 *
 * 为什么原来会漏：六个页面里各抄了自己的转义，一共 7 份（4 份 `escapeHtml` + 2 份 `esc`
 * + 1 份 `escQ`）。其中 4 份只转 `& < >`、**引号一个不转**，剩下 3 份补了双引号却**没有一份
 * 转单引号**。而 `${x}` 塞进 `title="…"`、`value="…"`
 * 这种属性位置时，不转引号就等于把属性值的右引号提前关掉——
 * `title="a" onmouseover="alert(1)` 后面接上 `">`，属性就变成事件处理器了。
 * `title="a" onmouseover="alert(1)` 后面接上 `">`，属性就变成事件处理器了。
 * 所以这一份把五种字符全转了：**同一个函数同时管文本上下文和属性上下文**，
 * 用的人不用先判断"我这处在不在引号里"。
 *
 * 它管不到的地方（这条比函数本身重要）：
 *   ❌ `onclick="doIt('${id}')"` —— 内联 JS 的字符串字面量里，`&#39;` 会被 HTML
 *      **先解码回 `'`** 再交给 JS 解析，所以转义**挡不住**脚本注入。
 *      这种位置唯一正确的做法是别把数据写进内联脚本：改成 `data-id="…"` + addEventListener
 *      （`data-*` 里 `esc()` 是够的，因为属性值不经过 JS 解析）。
 *   ❌ `href="${u}"` —— 转义挡得住语法破坏，挡不住 `javascript:` 协议，要校验协议。
 *      本项目这类位置用的都是自己拼的 `/api/uploads/<id>/download`，id 走 encodeURIComponent。
 *
 * 用哪个名字：`esc`（短，属性/文本都用它）。`escapeHtml` / `escQ` 这两个历史名字已删，
 * 出现第二个实现就是这次的修复被打穿，`tests/test_33_escape_single_source.py` 会红。
 */
(function (global) {
  "use strict";

  var CHARS = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  };

  /** 把任意值转成可以安全塞进 HTML 文本或带引号属性值的字符串。 */
  function esc(s) {
    // null/undefined 转成空串：调用点写 esc(x) 不该因为 x 缺失就打印出 "undefined"
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return CHARS[c];
    });
  }

  global.esc = esc;
})(window);

// 校验静态页内联 JS 的语法（页面是 HTML，node --check 直接读不了，先抽 <script> 内容）
//
// ⚠️ **不许写死文件清单**（#56-B 的起因）：这一版以前固定 `["confirm.html", "student.html"]` 两个文件，
//    后来长出 station / admin / plan / login / settings 五页，没人回来补这张名单 ——
//    于是「闸是全绿的、页面是坏的」。#54 正好改了 student / station / admin，其中两个当时根本不在闸里。
//    现在改成扫 `app/static/` 目录：以后加页面自动被覆盖，不需要谁记得来改这里。
const fs = require("fs");
const path = require("path");

// 从 tests/ 里跑还是从项目根跑都能找到目录（找不到时宁可报错，也不要"零个文件 = 全部通过"）
function pickDir() {
  const cands = ["app/static", path.join(__dirname, "..", "app", "static")];
  for (const d of cands) {
    try { if (fs.statSync(d).isDirectory()) return d; } catch (e) { /* 换下一个 */ }
  }
  return null;
}

const DIR = pickDir();
if (!DIR) {
  console.log("❌ 找不到 app/static 目录，这一趟**什么都没验到**（不该算通过）");
  process.exit(2);
}

const all = fs.readdirSync(DIR).sort();
const htmls = all.filter(f => f.endsWith(".html")).map(f => path.join(DIR, f));
const jss = all.filter(f => f.endsWith(".js")).map(f => path.join(DIR, f));

if (!htmls.length && !jss.length) {
  console.log("❌ " + DIR + " 下一个 .html / .js 都没找到，路径不对，这次没有验到任何东西");
  process.exit(2);
}

let bad = 0, checked = 0;

// 1) 页面里的内联 <script>（带 src= 的那种是外链，下面单独验文件本体）
for (const f of htmls) {
  const h = fs.readFileSync(f, "utf8");
  const blocks = [...h.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  console.log("=== " + f + " （内联 script " + blocks.length + " 块）===");
  blocks.forEach((m, i) => {
    const code = m[1];
    checked++;
    try {
      new Function(code);
      console.log("  块" + (i + 1) + "（" + code.length + " 字符）语法 OK");
    } catch (e) {
      bad++;
      console.log("  块" + (i + 1) + " 语法错误：" + e.message);
    }
  });
  // 页面一个内联块都没有、又确实是个页面：多半是那次改动把 <script> 标签弄坏了（正则配对不上）
  if (!blocks.length) { console.log("  ⚠️ 这页没有内联 script —— 要么它真的没脚本，要么标签被改坏了"); }
}

// 2) 外链的 .js 本体（`esc.js` 是全站唯一的转义函数：它语法错 = 每个页面的渲染都停摆）
for (const f of jss) {
  const code = fs.readFileSync(f, "utf8");
  checked++;
  try {
    new Function(code);
    console.log("=== " + f + " （" + code.length + " 字符）语法 OK ===");
  } catch (e) {
    bad++;
    console.log("=== " + f + " 语法错误：" + e.message + " ===");
  }
}

if (bad) {
  console.log("\n有 " + bad + " 处语法错误（上面点名了是哪个文件第几块）");
  process.exit(1);
}
console.log("\n全部通过（" + checked + " 处，覆盖 " + htmls.length + " 个页面 + " + jss.length + " 个外链脚本）");

// 校验静态页内联 JS 的语法（页面是 HTML，node --check 直接读不了，先抽 <script> 内容）
const fs = require("fs");
const files = ["app/static/confirm.html", "app/static/student.html"];
let bad = 0;
for (const f of files) {
  const h = fs.readFileSync(f, "utf8");
  const blocks = [...h.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  console.log("=== " + f + " （内联 script " + blocks.length + " 块）===");
  blocks.forEach((m, i) => {
    const code = m[1];
    try {
      new Function(code);
      console.log("  块" + (i + 1) + "（" + code.length + " 字符）语法 OK");
    } catch (e) {
      bad++;
      console.log("  块" + (i + 1) + " 语法错误：" + e.message);
    }
  });
}
if (bad) { console.log("\n有 " + bad + " 块语法错误"); process.exit(1); }
console.log("\n全部通过");

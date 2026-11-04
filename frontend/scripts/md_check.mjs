// Renders markdown through the real Md component (bundled with esbuild, drawn with react-dom/server) and prints JSON {name: html}.
// Used by backend/tests/test_md_sanitize.py. Run from the frontend folder:  node scripts/md_check.mjs
import { build } from "esbuild";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

const out = join(mkdtempSync(join(tmpdir(), "mdcheck-")), "md.cjs");
await build({ stdin: { contents: 'import React from "react"; import { renderToStaticMarkup } from "react-dom/server"; import Md from "./src/components/Md"; export const render = (md) => renderToStaticMarkup(React.createElement(Md, { md }));', resolveDir: resolve("."), loader: "ts" }, bundle: true, format: "cjs", platform: "node", outfile: out, jsx: "automatic", logLevel: "silent" });
const { render } = createRequire(import.meta.url)(out);
const cases = JSON.parse(readFileSync(0, "utf-8"));
const result = {};
for (const [name, md] of Object.entries(cases)) result[name] = render(md);
console.log(JSON.stringify(result));

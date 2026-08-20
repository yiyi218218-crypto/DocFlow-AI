#!/usr/bin/env node
/**
 * Parse every ```mermaid block in the repo's markdown and fail on syntax errors.
 *
 * A broken diagram renders as a red error box on GitHub, which is worse than no
 * diagram at all — and you only find out after pushing. This runs in CI.
 *
 *   node scripts/check-mermaid.mjs [rootDir]
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = process.argv[2] ?? process.cwd();
const SKIP = new Set(['node_modules', '.git', '.next', '.venv', 'dist', 'build', '.turbo']);

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  pretendToBeVisual: true,
  url: 'http://localhost/',
});
for (const key of ['window', 'document', 'HTMLElement', 'SVGElement', 'Element', 'Node', 'DOMParser']) {
  globalThis[key] = key === 'window' ? dom.window : dom.window[key];
}
Object.defineProperty(globalThis, 'navigator', {
  value: dom.window.navigator,
  configurable: true,
  writable: true,
});
globalThis.getComputedStyle = dom.window.getComputedStyle;

const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (entry.endsWith('.md')) yield full;
  }
}

function* blocks(src) {
  const lines = src.split('\n');
  let open = false;
  let start = 0;
  let buf = [];
  for (let i = 0; i < lines.length; i++) {
    if (!open && /^\s*```mermaid\s*$/.test(lines[i])) {
      open = true;
      start = i + 1;
      buf = [];
    } else if (open && /^\s*```\s*$/.test(lines[i])) {
      open = false;
      yield { line: start, code: buf.join('\n') };
    } else if (open) {
      buf.push(lines[i]);
    }
  }
}

let total = 0;
let failed = 0;

for (const file of walk(ROOT)) {
  for (const { line, code } of blocks(readFileSync(file, 'utf8'))) {
    total++;
    const where = `${relative(ROOT, file)}:${line}`;
    try {
      await mermaid.parse(code);
      console.log(`  ok    ${where}`);
    } catch (err) {
      failed++;
      console.error(`  FAIL  ${where}`);
      console.error(`        ${String(err.message).split('\n').slice(0, 6).join('\n        ')}`);
    }
  }
}

console.log(`\n${total - failed}/${total} mermaid blocks parsed`);
process.exit(failed > 0 ? 1 : 0);

#!/usr/bin/env node
/**
 * Copy the pdf.js worker into /public.
 *
 * pdf.js loads its worker as a separate asset at runtime. Next.js does not
 * bundle it, and a `?url` import does not resolve during `next build`, so the
 * reliable approach is to publish it as a static file and point workerSrc at a
 * stable path. The version is pinned by whatever is installed, so this runs on
 * every build rather than being committed.
 */
import { copyFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const pdfjsRoot = dirname(require.resolve('pdfjs-dist/package.json'));
const source = join(pdfjsRoot, 'build', 'pdf.worker.min.mjs');

mkdirSync('public', { recursive: true });
copyFileSync(source, join('public', 'pdf.worker.min.mjs'));
console.log('copied pdf.worker.min.mjs -> public/');

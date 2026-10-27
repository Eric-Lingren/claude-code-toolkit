#!/usr/bin/env node
/**
 * browser-verify.mjs — Playwright-based browser verification runner.
 *
 * Executes a declarative Check Spec against a running dev server and writes
 * a browser-check-result-v2 manifest to the output directory.
 *
 * Usage:
 *   node browser-verify.mjs \
 *     --spec   <path-to-check-spec.json | JSON-string> \
 *     --base-url <url>                                 \
 *     --state  <storage-state-path | null>             \
 *     --out    <output-dir>                            \
 *     [--baseline-dir <dir>]   # dir with baseline PNGs for diff
 *
 * Check Spec format:
 * {
 *   "role":      "default",           // optional
 *   "viewports": [{"width":1440,"height":900}],  // optional, defaults to desktop
 *   "masks":     ["[data-testid='clock']"],       // optional CSS selectors to mask
 *   "steps": [
 *     {"type": "goto",    "url": "/"},
 *     {"type": "capture", "name": "home"},
 *     {"type": "click",   "selector": "button[type='submit']"},
 *     {"type": "click",   "role": "button", "name": "Login"},
 *     {"type": "fill",    "selector": "#email", "value": "x@example.com"},
 *     {"type": "waitFor", "selector": ".dashboard"},
 *     {"type": "waitFor", "text": "Welcome"},
 *     {"type": "expect",  "selector": "h1", "text": "Home"},
 *     {"type": "expect",  "role": "button", "name": "Logout", "visible": true}
 *   ]
 * }
 *
 * Output: <out>/result.json (browser-check-result-v2)
 *         <out>/<name>-<WxH>.png           per capture
 *         <out>/<name>-<WxH>-diff.png      per capture when --baseline-dir provided
 *
 * Playwright is resolved from CWD (target project) then from this script's
 * own node_modules.  pixelmatch + pngjs are resolved from this script's
 * node_modules then from CWD; diffing is silently disabled when unavailable.
 */

import { createRequire }           from 'module';
import { fileURLToPath }           from 'url';
import { dirname, join, resolve }  from 'path';
import { createHash }              from 'crypto';
import { existsSync, mkdirSync,
         readFileSync, writeFileSync } from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);

// ── Arg parsing ─────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const out = {
    spec: null, baseUrl: null, state: null,
    outDir: null, baselineDir: null,
  };
  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case '--spec':         out.spec        = argv[++i]; break;
      case '--base-url':     out.baseUrl     = argv[++i]; break;
      case '--state':        out.state       = argv[++i]; break;
      case '--out':          out.outDir      = argv[++i]; break;
      case '--baseline-dir': out.baselineDir = argv[++i]; break;
      default:
        if (argv[i].startsWith('--'))
          console.warn(`browser-verify: unknown flag ignored: ${argv[i]}`);
    }
  }
  return out;
}

// ── Module resolution ────────────────────────────────────────────────────────

/**
 * Try to require `name` from each of the given base directories in order.
 * Returns the first successful result, or null.
 */
function tryRequireFrom(dirs, name) {
  for (const dir of dirs) {
    try {
      const req = createRequire(join(dir, '_placeholder_'));
      return req(name);
    } catch {}
  }
  return null;
}

// ── Determinism ──────────────────────────────────────────────────────────────

/** Injected before every navigation to freeze Date and Math.random. */
const DETERMINISM_INIT_SCRIPT = `
(function () {
  const FROZEN_TS = 1700000000000; // 2023-11-14T22:13:20Z
  const _Date = Date;
  class FrozenDate extends _Date {
    constructor(...a) { super(...(a.length ? a : [FROZEN_TS])); }
    static now()   { return FROZEN_TS; }
    static parse(s) { return _Date.parse(s); }
    static UTC(...a) { return _Date.UTC(...a); }
  }
  Object.defineProperty(globalThis, 'Date', {
    value: FrozenDate, writable: true, configurable: true,
  });
  // Seed Math.random to a constant sequence
  let _seed = 42;
  Math.random = function () {
    _seed = (_seed * 1664525 + 1013904223) >>> 0;
    return _seed / 4294967296;
  };
})();
`;

/** CSS injected after every goto to kill transitions/animations. */
const DISABLE_ANIMATIONS_CSS = `
*, *::before, *::after {
  animation-duration:   0s !important;
  animation-delay:      0s !important;
  transition-duration:  0s !important;
  transition-delay:     0s !important;
}
`;

// ── Locator helper ───────────────────────────────────────────────────────────

function buildLocator(page, step) {
  if (step.role)     return page.getByRole(step.role, step.name ? { name: step.name } : {});
  if (step.text)     return page.getByText(step.text, { exact: false });
  if (step.label)    return page.getByLabel(step.label);
  if (step.selector) return page.locator(step.selector);
  throw new Error(`step has no selector/role/text/label: ${JSON.stringify(step)}`);
}

// ── Pixel diff ────────────────────────────────────────────────────────────────

async function diffImages({ baselinePath, candidatePath, diffPath, pixelmatch, PNG }) {
  /** Decode a PNG file to a pngjs PNG object. */
  const readPng = (p) => new Promise((res, rej) => {
    const png = new PNG();
    png.on('parsed', function () { res(this); });
    png.on('error', rej);
    png.parse(readFileSync(p));
  });

  const [baseline, candidate] = await Promise.all([
    readPng(baselinePath),
    readPng(candidatePath),
  ]);

  if (baseline.width !== candidate.width || baseline.height !== candidate.height) {
    return {
      diffRatio: 1.0,
      diffNote:  `dimensions differ: baseline ${baseline.width}x${baseline.height} vs candidate ${candidate.width}x${candidate.height}`,
    };
  }

  const { width, height } = baseline;
  const diff = new PNG({ width, height });

  const diffPixels = pixelmatch(
    baseline.data,
    candidate.data,
    diff.data,
    width, height,
    { threshold: 0.1, includeAA: false },
  );

  const diffBuf = PNG.sync.write(diff);
  writeFileSync(diffPath, diffBuf);

  return { diffRatio: diffPixels / (width * height) };
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!args.spec || !args.baseUrl || !args.outDir) {
    console.error(
      'Usage: browser-verify.mjs --spec <spec> --base-url <url> --state <path|null> --out <dir>',
    );
    process.exit(1);
  }

  // ── Load spec ──────────────────────────────────────────────────────────────
  let spec;
  const specStr = args.spec.trim();
  if (specStr.startsWith('{')) {
    spec = JSON.parse(specStr);
  } else {
    spec = JSON.parse(readFileSync(specStr, 'utf8'));
  }

  const specHash = createHash('sha256')
    .update(JSON.stringify(spec))
    .digest('hex')
    .slice(0, 12);

  // ── Prepare output dir ─────────────────────────────────────────────────────
  const outDir = resolve(args.outDir);
  mkdirSync(outDir, { recursive: true });

  // ── Storage state ──────────────────────────────────────────────────────────
  const storageState =
    args.state && args.state !== 'null' ? resolve(args.state) : undefined;

  // ── Resolve playwright ─────────────────────────────────────────────────────
  const resolveDirs = [process.cwd(), __dirname];
  let pwModule =
    tryRequireFrom(resolveDirs, 'playwright') ||
    tryRequireFrom(resolveDirs, '@playwright/test');

  if (!pwModule) {
    console.error(
      'browser-verify: playwright not found.\n' +
      'Install it in the target project: npm i -D playwright\n' +
      'Or globally: npm i -g playwright',
    );
    process.exit(1);
  }
  const { chromium } = pwModule;

  // ── Resolve pixelmatch + pngjs (optional) ──────────────────────────────────
  // Prefer the script-local node_modules so callers don't need these deps.
  const diffResolveDirs = [__dirname, process.cwd()];
  const pixelmatch = tryRequireFrom(diffResolveDirs, 'pixelmatch');
  const pngjsMod   = tryRequireFrom(diffResolveDirs, 'pngjs');
  const PNG        = pngjsMod?.PNG;

  const diffEnabled = !!(pixelmatch && PNG && args.baselineDir);
  if (args.baselineDir && !diffEnabled) {
    console.warn(
      'browser-verify: warning: pixelmatch or pngjs not available — ' +
      'diff generation disabled. Run: npm install in claude-code-shared/scripts/',
    );
  }

  // ── Per-viewport runs ──────────────────────────────────────────────────────
  const viewports   = spec.viewports ?? [{ width: 1440, height: 900 }];
  const role        = spec.role ?? 'default';
  const masks       = spec.masks ?? [];
  const viewportResults = [];

  for (const viewport of viewports) {
    const vLabel  = `${viewport.width}x${viewport.height}`;
    const consoleErrors       = [];
    const unhandledRejections = [];
    const stepResults         = [];
    const captures            = [];
    let   viewportStatus      = 'pass';

    // Launch a fresh browser per viewport for full isolation
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport,
      ...(storageState ? { storageState } : {}),
    });
    const page = await context.newPage();

    // Inject determinism before any navigation
    await page.addInitScript(DETERMINISM_INIT_SCRIPT);

    // Capture console errors and unhandled rejections
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => {
      unhandledRejections.push(err.message);
    });

    try {
      for (const step of (spec.steps ?? [])) {
        const sr = { type: step.type, status: 'pass' };
        if (step.name     !== undefined) sr.name     = step.name;
        if (step.url      !== undefined) sr.url      = step.url;
        if (step.selector !== undefined) sr.selector = step.selector;
        if (step.role     !== undefined) sr.role     = step.role;
        if (step.text     !== undefined) sr.text     = step.text;

        try {
          switch (step.type) {
            // ── goto ────────────────────────────────────────────────────────
            case 'goto': {
              const url = /^https?:\/\//.test(step.url)
                ? step.url
                : `${args.baseUrl}${step.url}`;
              await page.goto(url, { waitUntil: 'networkidle', timeout: 30_000 });
              // Kill CSS transitions/animations after navigation
              await page.addStyleTag({ content: DISABLE_ANIMATIONS_CSS });
              // Wait for fonts
              await page.evaluate(() => document.fonts.ready);
              // Apply masks: hide dynamic regions with a solid overlay
              if (masks.length > 0) {
                await page.evaluate((selectors) => {
                  for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                      el.style.visibility = 'hidden';
                    }
                  }
                }, masks);
              }
              break;
            }

            // ── click ───────────────────────────────────────────────────────
            case 'click': {
              const loc = buildLocator(page, step);
              await loc.click({ timeout: 10_000 });
              break;
            }

            // ── fill ────────────────────────────────────────────────────────
            case 'fill': {
              const loc = buildLocator(page, step);
              await loc.fill(step.value ?? '', { timeout: 10_000 });
              break;
            }

            // ── waitFor ─────────────────────────────────────────────────────
            case 'waitFor': {
              const loc = buildLocator(page, step);
              await loc.waitFor({ state: 'visible', timeout: 15_000 });
              break;
            }

            // ── capture ─────────────────────────────────────────────────────
            case 'capture': {
              if (!step.name) throw new Error('capture step requires a "name" field');
              const pngName = `${step.name}-${vLabel}.png`;
              const pngPath = join(outDir, pngName);
              await page.screenshot({ path: pngPath, fullPage: true });
              sr.png_path = pngName;

              const captureEntry = {
                name:         step.name,
                viewport:     vLabel,
                png_path:     pngName,
                baseline_png: null,
                diff_png:     null,
                diffRatio:    null,
              };

              if (diffEnabled) {
                const baselinePng = join(args.baselineDir, pngName);
                if (existsSync(baselinePng)) {
                  const diffName = `${step.name}-${vLabel}-diff.png`;
                  const diffPath = join(outDir, diffName);
                  try {
                    const { diffRatio, diffNote } = await diffImages({
                      baselinePath: baselinePng,
                      candidatePath: pngPath,
                      diffPath,
                      pixelmatch,
                      PNG,
                    });
                    captureEntry.baseline_png = baselinePng;
                    captureEntry.diff_png     = diffPath;
                    captureEntry.diffRatio    = diffRatio;
                    if (diffNote) captureEntry.diff_note = diffNote;
                  } catch (e) {
                    captureEntry.diff_error = e.message;
                    console.warn(`browser-verify: diff failed for ${pngName}: ${e.message}`);
                  }
                }
              }

              captures.push(captureEntry);
              break;
            }

            // ── expect ──────────────────────────────────────────────────────
            case 'expect': {
              const loc = buildLocator(page, step);
              if (step.text !== undefined) {
                const actual = await loc.textContent({ timeout: 5_000 });
                if (!actual?.includes(step.text)) {
                  throw new Error(
                    `expect text "${step.text}" not found in "${actual?.slice(0, 120)}"`,
                  );
                }
              } else {
                // Default: assert element is visible
                await loc.waitFor({ state: 'visible', timeout: 5_000 });
              }
              break;
            }

            default:
              console.warn(`browser-verify: unknown step type "${step.type}" — skipped`);
              sr.status = 'skip';
          }
        } catch (err) {
          sr.status = 'fail';
          sr.error  = err.message;
          viewportStatus = 'fail';
          console.error(`browser-verify: step ${step.type} failed: ${err.message}`);
          // Continue executing remaining steps to collect all evidence
        }

        stepResults.push(sr);
      }
    } finally {
      await browser.close();
    }

    viewportResults.push({
      viewport,
      viewport_label:       vLabel,
      steps:                stepResults,
      captures,
      console_errors:       consoleErrors,
      unhandled_rejections: unhandledRejections,
      status:               viewportStatus,
    });
  }

  // ── Build result.json ──────────────────────────────────────────────────────
  const overallStatus = viewportResults.every((r) => r.status === 'pass') ? 'pass' : 'fail';

  const result = {
    schema_version:   'browser-check-result-v2',
    spec_hash:        specHash,
    base_url:         args.baseUrl,
    role,
    viewport_results: viewportResults,
    status:           overallStatus,
  };

  const resultPath = join(outDir, 'result.json');
  writeFileSync(resultPath, JSON.stringify(result, null, 2));

  console.log(`browser-verify: result written to ${resultPath}`);
  console.log(`browser-verify: status=${overallStatus}`);

  if (overallStatus === 'fail') process.exit(1);
}

main().catch((err) => {
  console.error(`browser-verify: fatal: ${err.message}`);
  process.exit(1);
});

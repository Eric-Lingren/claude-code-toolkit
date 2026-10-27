#!/usr/bin/env node
// Runner scaffold for the build-eval / hillclimb loop. Copy this into the
// user's repo and fill in loadCases / runCase / gradeCase below - the I/O
// shape, file naming, resume, and CLI surface are already hillclimb-ready
// so adding v2, v3, ... is `--variant v3`, not a refactor.
//
//   node claude-code-shared/evals/persona-accuracy/run-eval.mjs \
//     --flow .claude/hillclimb/persona-accuracy --variant baseline --model claude-haiku-4-5 --reps 3
//
// Structural properties this encodes (so you don't have to remember them):
//   - parameterized by --variant / --model / --reps (no hardcoded A/B pair)
//   - rep-aware filenames + resume (traces/<id>_rep<k>.json)
//   - reads _state.json, never writes it (loop state belongs to the orchestrator) - 
//     the ONE exception is --approve-harness recording `harness_sha` (see below)
//   - refuses to run when the harness (this file + _state.json.harness_paths) has
//     changed since the sha a human last approved with --approve-harness, so a
//     round that edits the runner cannot execute unreviewed under a standing
//     session allowlist
//   - pairwise graders judge against frozen baseline/ref/<id>.* on disk
//   - writes rows as cases complete (crash-safe)
//   - jittered exponential backoff on transient 429/overloaded/5xx errors
//   - hard per-case wall-clock ceiling (--timeout-s; stream keepalives don't reset it)
//   - served-model assertion (response model must match --model; documented alias->snapshot
//     shapes tolerated: 'foo-latest'/'foo-0'/'foo' -> 'foo-20250101' / 'foo@20250101' / 'foo-2025-01-01')
//   - failed attempts land in errors.jsonl with a failure class and, when the call
//     completed, the billed model/usage (never in results.jsonl)
//   - row ids, trace filenames, and frozen refs share one path-safe id
//     (original id kept in meta.original_id when sanitization changed it)

import { createHash } from 'node:crypto';
import { closeSync, constants as FS, existsSync, fstatSync, ftruncateSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, writeFileSync, writeSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Every output write refuses symlinks: the flow dir is model-influenced, and a
// prompt-injected round can plant `results.jsonl -> ~/.bashrc` where the next
// unattended run would append. POSIX opens O_NOFOLLOW (a symlink fails with
// ELOOP); Windows - where Node leaves O_NOFOLLOW undefined and Bun defines a
// meaningless value - lstat-refuses first. Symlinked parent dirs are
// refused the same way. Same discipline as the report builders' reads.
const WIN = process.platform === 'win32';
const NOFOLLOW = WIN ? 0 : FS.O_NOFOLLOW;
// A guard that cannot tell must refuse: only "no such entry" reads as absent;
// any other lstat failure (EACCES, ENAMETOOLONG, ...) is rethrown, never "no".
const lstatOrNull = p => { try { return lstatSync(p); } catch (e) { if (e?.code === 'ENOENT') return null; throw e; } };
const isSymlink = p => lstatOrNull(p)?.isSymbolicLink() === true;
// Stderr lines interpolate model-influenced bytes (case ids, error text that
// can echo model output, JSON.parse messages). Strip escape sequences and
// control characters, as build-report-lite.mjs's eprint does, so a planted
// OSC/CSI can't retitle the terminal or forge output lines.
const ESC_SEQ = /\x1b\[[0-?]*[ -\/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]/g;
const CONTROL = /[\x00-\x1f\x7f-\x9f]/g;
const termSafe = s => String(s).replace(ESC_SEQ, '').replace(CONTROL, '');
const eprint = (...a) => console.error(...a.map(termSafe));
// The leaf checks above can't see a symlink on an INTERMEDIATE component
// (lstat and open both resolve those silently), so every open is also bound
// to the flow root: main() captures realpathSync(flow) once, and any path
// whose resolved parent leaves it - e.g. `vdir` or the flow dir itself
// replaced by a directory symlink - is refused when the check sees it.
// Residual, all platforms: the check and the open are separate path lookups
// (Node's sync fs has no openat-style call), so a directory swapped for a
// symlink in between is still followed. This stops a planted link, not a
// writer racing the run.
let flowRealRoot = null;
function assertInFlow(dir, what) {
  if (flowRealRoot == null) throw new Error(`refusing to ${what}: flow root not resolved yet`);
  const dirReal = realpathSync(dir);
  if (dirReal !== flowRealRoot && !dirReal.startsWith(flowRealRoot + (WIN ? '\\' : '/')))
    throw new Error(`refusing to ${what}: ${dir} resolves outside the flow directory`);
}
function openNoFollow(p, flags) {
  if (isSymlink(dirname(p))) throw new Error(`refusing to open through symlinked directory: ${dirname(p)}`);
  assertInFlow(dirname(p), 'open');
  if (WIN && isSymlink(p)) throw new Error(`refusing to open through symlink: ${p}`);
  const fd = openSync(p, flags | NOFOLLOW, 0o644);
  try {
    const st = fstatSync(fd);
    if (!st.isFile()) throw new Error(`refusing to use non-regular file: ${p}`);
    // O_NOFOLLOW and lstat cannot see a hard link: a second name for a file
    // outside the flow dir opens as an ordinary regular file. Nothing the
    // runner creates has more than one link, so refuse any that does.
    if (st.nlink > 1) throw new Error(`refusing to use ${p}: it has a second hard link (another name for the same file); replace it with a plain copy if it is yours`);
  } catch (e) { closeSync(fd); throw e; }
  return fd;
}
// writeFileSync on the fd loops until every byte lands (a bare writeSync is
// one write(2) that may return short on ENOSPC and silently truncate a
// results row or trace).
// Opened without O_TRUNC and truncated only after openNoFollow's checks, so a
// refused file keeps its bytes.
function writeFileNoFollow(p, data) {
  const fd = openNoFollow(p, FS.O_WRONLY | FS.O_CREAT);
  try { ftruncateSync(fd, 0); writeFileSync(fd, data); } finally { closeSync(fd); }
}
// POSIX appends atomically under O_APPEND with no position. On Windows, Bun
// writes an O_APPEND handle at offset 0 unless given a position, so there the
// write starts at the current size and re-issues any short write.
function appendFileNoFollow(p, data) {
  const fd = openNoFollow(p, FS.O_WRONLY | FS.O_CREAT | FS.O_APPEND);
  try {
    if (!WIN) { writeFileSync(fd, data); return; }
    const buf = Buffer.from(data);
    const start = fstatSync(fd).size;
    for (let off = 0; off < buf.length;) {
      const n = writeSync(fd, buf, off, buf.length - off, start + off);
      if (n <= 0) throw new Error(`append to ${p} made no progress`);
      off += n;
    }
  } finally { closeSync(fd); }
}
// Reads of the frozen pairwise refs get the same discipline as writes (same
// open guard): the flow dir is model-influenced, so `baseline/ref/<id> ->
// ~/.ssh/id_rsa` planted after the startup preflight must not be read into
// the judge prompt. lexists probes with lstat so a planted symlink still
// counts as "present" at the freeze guard (never overwritten - or followed).
const lexists = p => lstatOrNull(p) != null;
function readFileNoFollow(p) {
  const fd = openNoFollow(p, FS.O_RDONLY);
  try { return readFileSync(fd, 'utf8'); } finally { closeSync(fd); }
}
// null when the file is absent; any other failure (a planted link included) throws.
function readIfPresent(p) {
  try { return readFileNoFollow(p); } catch (e) { if (e?.code === 'ENOENT') return null; throw e; }
}
function mkdirNoFollow(dir) {
  if (isSymlink(dir)) throw new Error(`refusing to use symlinked directory: ${dir}`);
  mkdirSync(dir, { recursive: true });
  // Check after creating: mkdirSync(recursive) follows symlinked ancestors,
  // so a dir minted through one resolves outside the flow root and is refused
  // here before any file lands in it.
  assertInFlow(dir, 'create directory');
}
// Frozen pairwise refs may carry an extension; reader and freeze-guard probe
// the same list so a suffixed ref never gets an extensionless shadow.
const REF_EXTS = ['', '.html', '.txt', '.json'];

// --- fill these in ----------------------------------------------------------

// persona-accuracy: runs the real agent file through
// `claude -p --agent persona-accuracy` against a throwaway sandbox holding
// only seed.json + transcript.jsonl for the case. File names are neutral
// (never the case id) so the planted label can't leak through a path. The
// prompt matches what to-seed step 3b sends (seed_path, transcript_path,
// disposed-id lock list).

import { spawn } from 'node:child_process';
import { copyFileSync, mkdtempSync, rmSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';

const EVAL_DIR = dirname(fileURLToPath(import.meta.url));
const CLAUDE_BIN = process.env.CLAUDE_BIN
  || join(homedir(), '.local/share/fnm/node-versions/v24.19.0/installation/bin/claude');
// Pinned, not inherited: a shell exporting a CLAUDE_CONFIG_DIR without the
// shared agents/ dir silently loads only built-in agents. EVAL_CLAUDE_CONFIG_DIR overrides.
const CLAUDE_CONFIG_DIR = process.env.EVAL_CLAUDE_CONFIG_DIR || join(homedir(), '.cco');
const AGENT = 'persona-accuracy';
const FIX = join(EVAL_DIR, 'fixtures');

// EVAL_ONLY=id1,id2 limits the run to those cases (pilots).
// EVAL_FAKE=oracle|null skips the CLI and feeds a perfect / empty answer
// through the grader, a free check that the pipeline scores ~100% / ~0%.
const ONLY = process.env.EVAL_ONLY ? new Set(process.env.EVAL_ONLY.split(',')) : null;
const FAKE = process.env.EVAL_FAKE;
if (!FAKE && !existsSync(join(CLAUDE_CONFIG_DIR, 'agents', 'seed-review', 'persona-accuracy.md'))) {
  eprint(`refusing to run: persona-accuracy agent not found under ${CLAUDE_CONFIG_DIR}/agents/seed-review/`);
  process.exit(2);
}

// Persona-visible transcript text: every string value in the JSONL, so
// transcript_span quotes compare against decoded text, not JSON escapes.
const transcriptText = new Map();
function textOf(base) {
  if (!transcriptText.has(base)) {
    const parts = [];
    const walk = v => { if (typeof v === 'string') parts.push(v); else if (v && typeof v === 'object') Object.values(v).forEach(walk); };
    for (const ln of readFileSync(join(FIX, base, 'transcript.jsonl'), 'utf8').split('\n')) {
      if (!ln.trim()) continue;
      try { walk(JSON.parse(ln)); } catch {}
    }
    transcriptText.set(base, norm(parts.join('\n')));
  }
  return transcriptText.get(base);
}
const norm = s => String(s).replace(/\s+/g, ' ').trim();

async function loadCases() {
  return readFileSync(join(EVAL_DIR, 'cases.legacy.jsonl'), 'utf8').split('\n').filter(Boolean)
    .filter(l => !ONLY || ONLY.has(JSON.parse(l).id)).map(l => {
      const c = JSON.parse(l);
      c.prompt = PROMPT('<sandbox>/seed.json', '<sandbox>/transcript.jsonl');
      c.meta = { base: c.base, expected: c.expected };
      return c;
    });
}

const PROMPT = (seed, tr) => `seed_path: ${seed}
transcript_path: ${tr}

Disposed-id lock list (do not raise threads with these ids): []

Hunt semantic drift and stale-resolution in the seed. Return a JSON array of refutations ([] if none).`;

function runCli(args, opts) {
  return new Promise((res, rej) => {
    const p = spawn(CLAUDE_BIN, args, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    p.stdout.on('data', d => { out += d; });
    p.stderr.on('data', d => { err += d; });
    p.on('error', rej);
    p.on('close', code => res({ code, out, err }));
  });
}

// stream-json events -> report Turn[] + final text + tool-call count.
function toTranscript(events, prompt) {
  const t = [{ role: 'user', content: prompt }];
  let final = '', toolCalls = 0;
  for (const ev of events) {
    const content = ev.message?.content;
    if (!Array.isArray(content)) continue;
    if (ev.type === 'assistant') {
      let thinking;
      for (const b of content) {
        if (b.type === 'thinking') thinking = (thinking ? thinking + '\n' : '') + (b.thinking || '');
        else if (b.type === 'text') { t.push({ role: 'assistant', content: b.text, ...(thinking ? { thinking } : {}) }); final = b.text; thinking = undefined; }
        else if (b.type === 'tool_use') { toolCalls++; t.push({ role: 'tool_call', name: b.name, content: JSON.stringify(b.input, null, 2), ...(thinking ? { thinking } : {}) }); thinking = undefined; }
      }
    } else if (ev.type === 'user') {
      for (const b of content) if (b.type === 'tool_result') {
        const c = Array.isArray(b.content) ? b.content.map(x => x.text ?? '').join('\n') : String(b.content ?? '');
        t.push({ role: 'tool_result', content: c.length > 4000 ? c.slice(0, 4000) + '\n...[trace truncated]' : c });
      }
    }
  }
  return { transcript: t, final, toolCalls };
}

// EVAL_FAKE=replay feeds saved replies from EVAL_REPLAY_DIR/<id>.txt (regrade
// without spend). Only for scratch flows: replay rows carry no real usage.
function fakeOutput(input) {
  const e = input.expected;
  if (FAKE === 'null') return '';
  if (FAKE === 'replay') return readFileSync(join(process.env.EVAL_REPLAY_DIR, `${input.id}.txt`), 'utf8');
  if (e.kind !== 'planted') return '[]';
  const span = textOf(input.base).slice(0, 80);
  return JSON.stringify([{ persona: 'accuracy', field: e.field, claim: e.planted_text, problem: 'oracle', transcript_span: span }]);
}

async function runCase(input, ctx) {
  if (FAKE) {
    const output = fakeOutput(input);
    return { output, transcript: [{ role: 'user', content: input.prompt }, { role: 'assistant', content: output }],
             model: ctx.model, usage: { input_tokens: 1, output_tokens: 1 }, stop_reason: 'end_turn',
             toolCalls: 0, cli_cost_usd: 0, num_turns: 1 };
  }
  const box = mkdtempSync(join(tmpdir(), 'pacc-sandbox-'));
  try {
    copyFileSync(join(FIX, input.seed), join(box, 'seed.json'));
    copyFileSync(join(FIX, input.base, 'transcript.jsonl'), join(box, 'transcript.jsonl'));
    const prompt = PROMPT(join(box, 'seed.json'), join(box, 'transcript.jsonl'));
    const args = ['-p', prompt, '--agent', AGENT, '--output-format', 'stream-json', '--verbose',
                  '--allowedTools', 'Read,Grep',
                  // User-level hooks/plugins (SessionStart, UserPromptSubmit) never reach a
                  // production to-seed subagent spawn; keep them out of these runs too.
                  '--settings', JSON.stringify({ disableAllHooks: true, enabledPlugins: { 'caveman@caveman': false } }),
                  ...(ctx.model ? ['--model', ctx.model] : [])];
    // No mid-run self-update: it swaps the binary under in-flight spawns.
    const r = await runCli(args, { cwd: box, env: { ...process.env, CLAUDE_CONFIG_DIR, DISABLE_AUTOUPDATER: '1' } });
    const events = r.out.split('\n').filter(Boolean).flatMap(l => { try { return [JSON.parse(l)]; } catch { return []; } });
    const result = events.findLast(e => e.type === 'result');
    if (!result) {
      const e = new Error(`no result event (exit ${r.code}): ${r.err.slice(0, 300)}`);
      e.failure_class = 'harness';
      throw e;
    }
    if (result.is_error && /rate.?limit|overloaded|529|429/i.test(String(result.result))) {
      const e = new Error(String(result.result)); e.status = 429; throw e;
    }
    const { transcript, final, toolCalls } = toTranscript(events, prompt);
    // A usage limit hit mid-session ends with 'success' and the limit notice as
    // the reply text: infra, not a persona answer.
    if (/^You've hit your (session|usage|weekly) limit/i.test(final.trim())) {
      const e = new Error(`usage limit mid-session: ${final.trim().slice(0, 200)}`);
      e.failure_class = 'harness';
      throw e;
    }
    // The CLI can bill more than one model per session; the row's model is the costliest one.
    const mu = result.modelUsage || {};
    const model = Object.keys(mu).sort((a, b) => (mu[b].costUSD || 0) - (mu[a].costUSD || 0))[0];
    // A session that billed no model never reached the persona (broken CLI,
    // auth, startup error): an infra failure, not a scorable empty reply.
    if (!model) {
      const e = new Error(`no model billed (exit ${r.code}, ${result.num_turns} turns): ${String(result.result ?? r.err).slice(0, 300)}`);
      e.failure_class = 'harness';
      throw e;
    }
    return {
      output: final, transcript, model, usage: result.usage,
      stop_reason: result.subtype === 'success' ? 'end_turn' : result.subtype,
      status: result.subtype === 'error_max_turns' ? 'truncated' : 'ok',
      toolCalls, cli_cost_usd: result.total_cost_usd, num_turns: result.num_turns,
      models_used: Object.keys(mu),
    };
  } finally {
    rmSync(box, { recursive: true, force: true });
  }
}

// Strict = the whole reply is one JSON array (the agent's output contract).
// Lenient = the last fenced block, else the widest [...] span, that parses to
// an array of objects (or []). Prose like `decisions[3]` parses as [3] and
// must never count as the reply.
const objArray = a => Array.isArray(a) && a.every(o => o && typeof o === 'object' && !Array.isArray(o));
function parseArray(text) {
  const s = String(text ?? '').trim();
  try { const a = JSON.parse(s); if (objArray(a)) return { arr: a, strict: true }; } catch {}
  const fences = [...s.matchAll(/```(?:json)?\s*\n([\s\S]*?)```/g)].map(m => m[1]).reverse();
  for (const f of fences) { try { const a = JSON.parse(f); if (objArray(a)) return { arr: a, strict: false }; } catch {} }
  for (let i = s.indexOf('['); i >= 0; i = s.indexOf('[', i + 1)) {
    for (let j = s.lastIndexOf(']'); j > i; j = s.lastIndexOf(']', j - 1)) {
      try { const a = JSON.parse(s.slice(i, j + 1)); if (objArray(a)) return { arr: a, strict: false }; } catch {}
    }
  }
  return { arr: null, strict: false };
}

const NORMAL_KEYS = ['persona', 'field', 'claim', 'problem', 'transcript_span'];
const validNormal = o => o && typeof o === 'object' && !Array.isArray(o)
  && Object.keys(o).every(k => NORMAL_KEYS.includes(k)) && NORMAL_KEYS.every(k => k in o)
  && o.persona === 'accuracy' && ['field', 'claim', 'problem'].every(k => typeof o[k] === 'string')
  && (o.transcript_span === null || typeof o.transcript_span === 'string');
const validError = o => o && typeof o === 'object' && typeof o.error === 'string' && o.persona === 'accuracy'
  && Object.keys(o).every(k => ['persona', 'error', 'details'].includes(k));
// 'decisions[3]' matches 'decisions[3]', 'decisions[3].text'; 'summary' matches 'summary', 'summary[1]'.
const onField = (got, want) => { const g = String(got ?? '').replace(/\s/g, ''); return g === want || g.startsWith(want + '.') || g.startsWith(want + '['); };

async function gradeCase(input, run) {
  const exp = input.expected;
  const { arr, strict } = parseArray(run.output);
  const items = arr ?? [];
  const errs = items.filter(o => o && 'error' in o);
  const refs = items.filter(o => o && !('error' in o));
  const covered = arr !== null && errs.length === 0;
  // A refutation targets the planted/distractor text when its field matches OR
  // its claim quotes the changed text (personas sometimes cite a line number as
  // the index; field_ok scores the addressing separately).
  const P = norm(exp.planted_text ?? '').toLowerCase();
  const quotesPlant = o => { const c = norm(o.claim ?? '').toLowerCase(); return !!P && (c.includes(P) || (c.length >= 20 && P.includes(c))); };
  const targets = refs.filter(o => onField(o.field, exp.field) || quotesPlant(o));
  const hit = targets.length > 0;
  let correct;
  if (exp.kind === 'planted') correct = covered && hit;
  else if (exp.kind === 'distractor') correct = covered && !hit;
  else correct = covered && refs.length === 0;
  const grade = { correct: correct ? 1 : 0 };
  if (exp.kind === 'planted') grade.recall = grade.correct;
  else grade.specificity = grade.correct;
  if (exp.kind === 'planted' && hit) grade.field_ok = targets.some(o => onField(o.field, exp.field)) ? 1 : 0;
  if (refs.length) {
    const T = textOf(input.base);
    grade.quote_ok = refs.every(o => typeof o.transcript_span === 'string' && o.transcript_span.trim()
      && T.includes(norm(o.transcript_span))) ? 1 : 0;
  }
  grade.format_ok = strict && items.every(o => validNormal(o) || validError(o)) ? 1 : 0;
  grade.covered = covered ? 1 : 0;
  const explanation = {
    correct: `expected ${exp.kind}${exp.field ? ` on ${exp.field}` : ''}; got ${arr === null ? 'unparseable' : `${refs.length} refutation(s) on [${refs.map(o => o.field).join(', ')}]`}`
      + (errs.length ? `; error-form: ${String(errs[0].error).slice(0, 200)}` : ''),
    ...(refs.length ? { quote_ok: refs.map(o => `${o.field}: ${String(o.transcript_span ?? 'null').slice(0, 140)}`).join(' | ') } : {}),
  };
  return { grade, explanation };
}

function perfFrom(run) {
  return { tool_calls: run.toolCalls, cli_cost_usd: run.cli_cost_usd, num_turns: run.num_turns, models_used: run.models_used };
}

// --- harness (you usually won't need to touch below this line) --------------

function parseArgs(argv) {
  const a = { flow: '.claude/hillclimb/flow', variant: 'baseline',
              model: undefined, reps: 1, concurrency: 4, timeoutS: 1800,
              approveHarness: false, regrade: false };
  // A flag at the end of argv would otherwise consume undefined - which for
  // --model equals the default and silently disables the served-model check.
  const val = (i) => { if (argv[i] === undefined) { eprint(`missing value for ${argv[i - 1]}`); usage(); process.exit(2); } return argv[i]; };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--flow') a.flow = val(++i);
    else if (k === '--variant') a.variant = val(++i);
    else if (k === '--model') a.model = val(++i);
    else if (k === '--reps') a.reps = +val(++i);
    else if (k === '--concurrency') a.concurrency = +val(++i);
    else if (k === '--timeout-s') a.timeoutS = +val(++i);
    else if (k === '--approve-harness') a.approveHarness = true;
    else if (k === '--regrade') a.regrade = true;
    else if (k === '-h' || k === '--help') { usage(); process.exit(0); }
    else { eprint(`unknown argument: ${k}`); usage(); process.exit(2); }
  }
  if (!/^(baseline|v[1-9]\d*)$/.test(a.variant)) {
    // The report only reads directories named 'baseline' or 'v<N>' - any other
    // name runs to completion but spends the pass into a directory the Summary,
    // trajectory, and budget arithmetic never see.
    eprint(`--variant must be 'baseline' or 'v<N>', got '${a.variant}'`);
    usage(); process.exit(2);
  }
  if (!Number.isFinite(a.timeoutS) || a.timeoutS < 0
      || a.timeoutS * 1000 > 2147483647 // setTimeout clamps >2^31-1 ms to 1 ms - the ceiling would fire instantly
      || !Number.isInteger(a.reps) || a.reps < 1
      || !Number.isInteger(a.concurrency) || a.concurrency < 1) { usage(); process.exit(2); }
  return a;
}
function usage() {
  eprint('usage: node run-eval.mjs --flow DIR --variant ID [--model ID] [--reps N] [--concurrency N] [--timeout-s N (0 = no ceiling)] [--approve-harness] [--regrade]');
}

// Harness integrity gate. The hillclimb loop gets this runner command
// allowlisted for the session and then runs rounds unattended, while the
// per-round change (proposed by an analyzer fed untrusted transcripts) may
// legitimately edit harness code. Without this gate a round that rewrites the
// runner would execute attacker-chosen code on the next unattended run under
// the user's one-time approval. So: sha256 over this file, any lockfile
// beside it or in the directory the runner is invoked from (dependency drift
// is harness drift too; with neither, a note says so), plus
// every path in `_state.json.harness_paths` (relative to the directory the
// runner is invoked from, i.e. the repo root); compare to
// `_state.json.harness_sha`; refuse on absent/mismatch unless a human passes
// --approve-harness, which records the new sha. That write is the one
// sanctioned exception to "never write _state.json".
//
// Scope, stated plainly: this is a change detector, not a security boundary.
// The sha, the path list and this file all live where the loop agent can
// write, so an agent that is already prompt-injected can forge the record or
// pass --approve-harness itself. What it catches is the common case - a
// round's harness edit reaching an unattended run unreviewed - and what
// actually bounds an unattended run is the permission allowlist the user
// grants the runner command (scope it to this exact command line, not a
// prefix). Installed dependencies beyond the lockfile are outside the digest.
function checkHarness(statePath, st, approve) {
  const self = fileURLToPath(import.meta.url);
  const listed = Array.isArray(st.harness_paths) ? st.harness_paths.map(String) : [];
  const lockfiles = [...new Set([dirname(self), process.cwd()].flatMap(d =>
    ['package-lock.json', 'bun.lock', 'bun.lockb', 'yarn.lock', 'pnpm-lock.yaml'].map(f => join(d, f))))]
    .filter(f => existsSync(f));
  const paths = [...new Set([self, ...lockfiles, ...listed.map(p => resolve(p))])].sort();
  const h = createHash('sha256');
  const hashed = [];
  for (const p of paths) {
    let buf;
    try { buf = readFileSync(p); }
    catch (e) {
      if (p === self) throw e;
      eprint(`warning: harness path '${relative(process.cwd(), p)}' not readable (${e?.code || 'error'}) - skipped`);
      continue;
    }
    h.update(relative(process.cwd(), p)).update('\0').update(buf).update('\0');
    hashed.push(relative(process.cwd(), p));
  }
  const sha = h.digest('hex');
  if (st.harness_sha === sha) return;
  // Said only here, where a person is about to approve or is being refused.
  if (!lockfiles.length) eprint('note: no lockfile beside the runner or in the current directory - dependency changes are outside the harness sha');
  if (approve) {
    st.harness_sha = sha;
    writeFileNoFollow(statePath, JSON.stringify(st, null, 2) + '\n');
    eprint(`harness approved: sha256 ${sha.slice(0, 12)} over ${hashed.length} file(s) recorded in ${statePath}`);
    return;
  }
  if (st.harness_sha == null) {
    eprint(`no approved harness sha in ${statePath} (computed ${sha.slice(0, 12)} over: ${hashed.join(', ')}).`);
    eprint('Review the harness, then run once with --approve-harness to record it.');
  } else {
    eprint(`harness changed since last approved run (files: ${hashed.join(', ')}); `
      + `approved ${String(st.harness_sha).slice(0, 12)}, now ${sha.slice(0, 12)}.`);
    eprint('Re-run with --approve-harness after reviewing the diff.');
  }
  process.exit(2);
}

// Transient provider errors (429 / overloaded / 5xx) retry with jittered
// exponential backoff - a zero-delay retry loop multiplies cost invisibly
// under rate limits and can turn one transient 429 into a torn-down batch.
// The attempt count lands in the row's meta (or the errors sidecar) so retry
// churn is visible in the data, not just the bill.
async function withBackoff(fn, retry, deadline = Infinity, tries = 5) {
  for (let attempt = 0; ; attempt++) {
    // Checked before every attempt, not just before sleeps: once the case's
    // ceiling has passed, an abandoned chain must not issue another call
    // (e.g. a judge call after the app call consumed the whole ceiling).
    if (Date.now() >= deadline) {
      const e = new Error('wall-clock ceiling exceeded before attempt');
      e.failure_class = 'timeout';
      throw e;
    }
    try { return await fn(); } catch (e) {
      const status = e?.status ?? e?.response?.status;
      const transient = status === 429 || status === 529 || (status >= 500 && status < 600)
        || /overloaded|rate.?limit/i.test(String(e?.message ?? ''));
      if (!transient || attempt >= tries - 1) throw e;
      const delay = Math.min(60_000, 1000 * 2 ** attempt) * (0.5 + Math.random());
      // Never start a retry that would outlive the case's wall-clock ceiling - 
      // otherwise an abandoned chain keeps issuing API calls after the case failed.
      if (Date.now() + delay >= deadline) throw e;
      retry.count++;
      await new Promise(r => setTimeout(r, delay));
    }
  }
}

// Hard per-case wall-clock ceiling, independent of stream liveness - a hung
// SSE stream can emit keepalives forever, defeating inactivity-based timers.
// The underlying call may keep running; the case fails and the slot is freed.
function withTimeout(promise, seconds, label) {
  if (!(seconds > 0)) return promise;
  let timer;
  const ceiling = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const e = new Error(`${label}: exceeded ${seconds}s wall-clock ceiling`);
      e.failure_class = 'timeout';
      reject(e);
    }, seconds * 1000);
  });
  return Promise.race([promise, ceiling]).finally(() => clearTimeout(timer));
}

// Case ids appear in file paths AND as the row/file join key the report uses,
// so rows, trace filenames, and frozen refs all carry the same path-safe id.
// When sanitization changes the id, a short content hash keeps distinct ids
// distinct ('case/1' vs 'case_1'); the original rides in meta.original_id.
function pathSafeId(id) {
  const raw = String(id);
  const cleaned = raw.replace(/[^\w.-]/g, '_');
  // Idempotent by construction: anything already path-safe and within the
  // length bound - including this function's own truncated+suffixed output - 
  // passes through unchanged. Long ids (URLs, prompt text as id) truncate to
  // 120 chars plus an 8-hex hash of the full original, so they fail here, not
  // at the trace write after the spend, and distinct ids stay distinct.
  if (cleaned === raw && raw.length <= 129) return raw;
  return `${cleaned.slice(0, 120)}-${createHash('sha256').update(raw).digest('hex').slice(0, 8)}`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  // lstat("link/") follows the final symlink, so a trailing separator on
  // --flow would blind every leaf isSymlink check below - strip it first.
  // Only Windows treats `\` as a separator; on POSIX it is a filename byte, so
  // splitting on it would walk prefixes that are not real path components.
  args.flow = args.flow.replace(WIN ? /(.)[\\/]+$/ : /(.)\/+$/, '$1');
  const flowSegments = args.flow.split(WIN ? /[\\/]/ : '/');
  // A `.`/`..` segment (e.g. a trailing `/.`) makes isSymlink(args.flow) below
  // resolve a different final component than the named dir - following a
  // planted link at the flow root - while join() collapses it and the absolute
  // branch skips the ancestor walk. Refuse dot segments outright
  // (absolute --flow stays supported).
  if (flowSegments.some(seg => seg === '.' || seg === '..')) {
    eprint(`refusing to run: --flow must not contain '.' or '..' segments, got '${args.flow}'`);
    process.exit(2);
  }
  const vdir = join(args.flow, args.variant);
  // Preflight every output path before the first model call: a planted
  // symlink would otherwise fail each case after its (billed) run.
  for (const p of [args.flow, join(args.flow, 'baseline'), vdir, join(vdir, 'traces'),
                   join(vdir, 'results.jsonl'), join(vdir, 'errors.jsonl'),
                   join(vdir, 'progress.txt'), join(args.flow, 'baseline', 'ref'), join(args.flow, '_state.json')])
    if (isSymlink(p)) { eprint(`refusing to run: ${p} is a symlink (the flow dir must hold regular files)`); process.exit(2); }
  // A relative --flow (the documented `.claude/hillclimb/<name>` layout) is
  // also lstat-walked component by component from the cwd: a pre-planted
  // link at an ancestor (`.claude/hillclimb -> elsewhere`) would otherwise
  // relocate the root capture below - the containment anchor itself - to the
  // attacker's target. An absolute --flow is the caller's own trust decision
  // and is not walked (an absolute ancestor link can be legitimate: /tmp on
  // macOS).
  if (!isAbsolute(args.flow)) {
    let walk = '';
    for (const part of flowSegments.filter(Boolean).slice(0, -1)) {
      walk = walk ? join(walk, part) : part;
      if (isSymlink(walk)) { eprint(`refusing to run: ${walk} is a symlink (ancestor of --flow)`); process.exit(2); }
    }
  }
  // Every later open/mkdir is bound to this resolved root (see assertInFlow):
  // create the flow dir when fresh (the preflight above refused a link at it
  // and, for a relative path, at every ancestor), then capture where it
  // really resolves.
  mkdirSync(args.flow, { recursive: true });
  flowRealRoot = realpathSync(args.flow);
  mkdirNoFollow(join(vdir, 'traces'));
  // _state.json is READ-ONLY here. The orchestrator owns it. Absent is fine
  // (a baseline-only run has no loop state yet), but present-and-unparsable
  // must not let the id-space gate below pass vacuously over a corrupt file.
  const statePath = join(args.flow, '_state.json');
  let st = {};
  // Read through the no-follow opener like every other flow-dir file; the
  // parse message is not echoed (it can quote the file's first bytes).
  const stateText = readIfPresent(statePath);
  if (stateText != null) {
    try { st = JSON.parse(stateText) || {}; }
    catch { eprint(`${statePath} exists but is not valid JSON - fix it before spending a pass`); process.exit(2); }
  }
  checkHarness(statePath, st, args.approveHarness);
  const ctx = { ...args, state: st };

  // Resume: which (id, rep) pairs already have a row?
  const resultsPath = join(vdir, 'results.jsonl');
  const done = new Set();
  for (const ln of (readIfPresent(resultsPath) ?? '').split('\n')) {
    if (!ln.trim()) continue;
    try { const r = JSON.parse(ln); done.add(`${r.prompt_id}\0${r.rep}`); } catch {}
  }
  // Rows key on the path-safe id (see pathSafeId), so resume must too.

  const cases = await loadCases();
  // Validate the id space before spending anything: duplicate path-safe ids - 
  // including case-insensitive twins, which macOS/Windows filesystems collapse - 
  // would silently overwrite traces and frozen refs; and a _state.json split id
  // that matches no case would silently shrink the scored denominator.
  const seen = new Map();
  for (const c of cases) {
    const k = pathSafeId(c.id).toLowerCase();
    if (seen.has(k)) {
      eprint(`duplicate case id after sanitization: '${c.id}' collides with '${seen.get(k)}'`);
      process.exit(2);
    }
    seen.set(k, c.id);
  }
  const safeIds = new Set(cases.map(c => pathSafeId(c.id)));
  for (const k of ['train_ids', 'val_ids', 'test_ids'])
    if (st[k] != null && !Array.isArray(st[k])) { eprint(`_state.json ${k} must be a list of ids`); process.exit(2); }
  for (const sid of [...(st.train_ids ?? []), ...(st.val_ids ?? []), ...(st.test_ids ?? [])]) {
    const s = String(sid); // the adapter joins with String() on both sides - numeric ids are fine
    if (safeIds.has(s)) continue; // matches a loaded case - definitionally valid
    if (s !== pathSafeId(s)) {
      // Can never match a row: rows key on path-safe ids. This is the silent
      // shrunken-denominator bug - fail before anything is spent.
      eprint(`_state.json split id '${s}' is not a path-safe id - record split ids exactly as they appear in results.jsonl's prompt_id`);
      process.exit(2);
    }
    // Well-formed but absent is legitimate (a trimmed top-K subset run) - note it, don't fail.
    eprint(`note: split id '${s}' matches no loaded case (expected for a trimmed subset run)`);
  }
  // --regrade: re-score this variant's existing rows from their stored traces
  // (final assistant turn = the reply) against the current cases/grader.
  // No model calls; tags/meta follow the current case row.
  if (args.regrade) {
    const byId = new Map(cases.map(c => [pathSafeId(c.id), c]));
    const out = []; let changed = 0, missing = 0;
    for (const ln of (readIfPresent(resultsPath) ?? '').split('\n')) {
      if (!ln.trim()) continue;
      const r = JSON.parse(ln);
      const c = byId.get(r.prompt_id);
      const tr = c && readIfPresent(join(vdir, 'traces', `${r.prompt_id}_rep${r.rep}.json`));
      if (!tr) { missing++; out.push(ln); continue; }
      const final = JSON.parse(tr).filter(t => t.role === 'assistant').at(-1)?.content ?? '';
      const { grade, explanation } = await gradeCase(c, { output: final });
      if (JSON.stringify(grade) !== JSON.stringify(r.grade)) changed++;
      out.push(JSON.stringify({ ...r, tags: c.tags, meta: c.meta, grade, explanation }));
    }
    writeFileNoFollow(resultsPath, out.join('\n') + '\n');
    eprint(`[${args.variant}] regraded ${out.length} rows: ${changed} grade(s) changed, ${missing} without trace (kept)`);
    return;
  }
  const refDir = join(args.flow, 'baseline', 'ref');
  const tasks = [];
  for (const c of cases) for (let rep = 0; rep < args.reps; rep++) {
    if (done.has(`${pathSafeId(c.id)}\0${rep}`)) continue;
    tasks.push({ c, rep });
  }
  eprint(`[${args.variant}] ${tasks.length} of ${cases.length * args.reps} (id,rep) to run`);

  let i = 0, ok = 0, fail = 0;
  const errorsPath = join(vdir, 'errors.jsonl');
  // A hard crash (power loss, ENOSPC) can leave a torn final line with no
  // trailing newline; the next append would merge two rows into one permanently
  // unparseable line. Isolate any fragment before appending anything.
  for (const p of [resultsPath, errorsPath]) {
    const tail = readIfPresent(p);
    if (tail && !tail.endsWith('\n')) appendFileNoFollow(p, '\n');
  }
  async function worker() {
    while (i < tasks.length) {
      const { c, rep } = tasks[i++];
      const safeId = pathSafeId(c.id);
      const t0 = Date.now();
      let lastRun = null;    // survives into the catch - billed spend on a failed attempt
      let rowWritten = false; // set once the results row lands - the attempt is scored
      const deadline = args.timeoutS > 0 ? t0 + args.timeoutS * 1000 : Infinity;
      const appRetry = { count: 0 }, judgeRetry = { count: 0 };
      try {
        // One ceiling over the whole case - app call, identity check, and grading - 
        // so a hung judge stream can't hold the slot either.
        const { run, g, latency_s } = await withTimeout((async () => {
          let tAttempt = t0;
          const run = await withBackoff(() => { tAttempt = Date.now(); return runCase(c, ctx); },
            appRetry, deadline);
          lastRun = run;
          // latency_s = the final app attempt only; backoff sleeps, failed
          // attempts, and judge time are excluded (retry counts are in meta).
          const latency_s = (Date.now() - tAttempt) / 1000;
          // Serving identity: fail loudly when the response was served by a model
          // other than the one requested. Accept exact match or a documented
          // alias->snapshot resolution - 'foo-latest'/'foo-0'/'foo' served as
          // 'foo-20250101', 'foo@20250101', or 'foo-2025-01-01'. Anything else - 
          // another snapshot of the requested pin, a sibling model, or the bare
          // base id ('foo-latest' served as 'foo', an unversioned echo that can
          // hide snapshot drift across rounds) - fails the attempt. Non-Anthropic
          // id schemes (e.g. Bedrock's 'anthropic.claude-...-v1:0') need their own
          // rule here.
          if (ctx.model && run.model && run.model !== ctx.model) {
            const base = ctx.model.replace(/-latest$|-0$/, '');
            const rest = String(run.model).startsWith(base)
              ? String(run.model).slice(base.length) : null;
            if (!(rest != null && /^[-@](\d{8}|\d{4}-\d{2}-\d{2})$/.test(rest))) {
              const e = new Error(`served model ${run.model} != requested ${ctx.model}`);
              e.failure_class = 'serving_substitution';
              throw e;
            }
          }
          // Frozen pairwise reference (never regenerated): baseline/ref/<id>.*
          let ref = null;
          if (args.variant !== 'baseline') {
            const p = join(refDir, safeId);
            // A planted symlink throws (ELOOP) rather than feeding the judge
            // its target; the case then fails loudly instead of leaking.
            for (const ext of REF_EXTS) {
              try { ref = readFileNoFollow(p + ext); break; }
              catch (e) { if (e?.code !== 'ENOENT') throw e; }
            }
          }
          const g = await withBackoff(() => gradeCase(c, run, ref, ctx), judgeRetry, deadline);
          return { run, g, latency_s };
        })(), args.timeoutS, `${c.id} rep${rep}`);
        const row = {
          prompt_id: safeId, rep, prompt: c.prompt ?? c.input ?? c.id,
          tags: c.tags, attachments: c.attachments,
          meta: safeId !== String(c.id) || appRetry.count || judgeRetry.count
            ? { ...(c.meta ?? {}),
                ...(safeId !== String(c.id) ? { original_id: String(c.id) } : {}),
                ...(appRetry.count ? { retries: appRetry.count } : {}),
                ...(judgeRetry.count ? { judge_retries: judgeRetry.count } : {}) }
            : c.meta,
          model: run.model, usage: run.usage, stop_reason: run.stop_reason,
          // The report keys on `status`, not stop_reason: a clipped answer is
          // counted and shown but kept out of the means. runCase may set
          // run.status to override the max_tokens rule.
          status: run.status ?? (run.stop_reason === 'max_tokens' ? 'truncated' : 'ok'),
          judge_model: g.judge_model ?? run.judge_model,
          judge_usage: g.judge_usage ?? run.judge_usage,
          latency_s, ...perfFrom(run),
          grade: g.grade, explanation: g.explanation,
        };
        appendFileNoFollow(resultsPath, JSON.stringify(row) + '\n');
        rowWritten = true; // past this point the attempt is scored - a later throw (trace write, ref freeze) must not also append an error row
        if (run.transcript)
          writeFileNoFollow(join(vdir, 'traces', `${safeId}_rep${rep}.json`),
            JSON.stringify(run.transcript, null, 2));
        // For pairwise: on the baseline run, freeze the reference output once.
        if (args.variant === 'baseline' && run.output != null
            && !REF_EXTS.some(ext => lexists(join(refDir, safeId) + ext))) {
          mkdirNoFollow(refDir);
          writeFileNoFollow(join(refDir, safeId),
            typeof run.output === 'string' ? run.output : JSON.stringify(run.output));
        }
        ok++;
      } catch (e) {
        fail++;
        if (rowWritten) {
          // The attempt scored; only a post-row write (trace, ref) failed. An error
          // row here would double-count the billed usage under the budget rule.
          eprint(`  [${args.variant}] ${c.id} rep${rep} scored, but a post-row write failed: ${e?.message || e}`);
          continue;
        }
        // Failed attempts are data too - but they must not occupy the (case, rep)
        // slot in results.jsonl, or resume would never re-run them.
        appendFileNoFollow(errorsPath, JSON.stringify({
          prompt_id: safeId, rep,
          ...(safeId !== String(c.id) ? { original_id: String(c.id) } : {}),
          failure_class: e?.failure_class ?? 'error',
          error: String(e?.message || e),
          retries: appRetry.count, judge_retries: judgeRetry.count,
          // Billed-but-failed spend stays countable: when the app call completed
          // before the failure (e.g. a served-model mismatch, a judge-stage
          // ceiling), carry its identity and usage on the error row.
          model: lastRun?.model, usage: lastRun?.usage,
          judge_model: e?.judge_model ?? lastRun?.judge_model,
          judge_usage: e?.judge_usage ?? lastRun?.judge_usage,
          latency_s: (Date.now() - t0) / 1000,
        }) + '\n');
        eprint(`  [${args.variant}] ${c.id} rep${rep} FAILED: ${e?.message || e}`);
      }
    }
  }
  // One progress line every 30s (and to <vdir>/progress.txt) so "how far along
  // is it?" is answerable from the background shell's output or one file read,
  // without the orchestrator parsing results.jsonl mid-write. ETA is a plain
  // rate extrapolation from this pass.
  const t0 = Date.now();
  const progress = () => {
    const done = ok + fail, total = tasks.length;
    const el = (Date.now() - t0) / 1000;
    const eta = done ? Math.round((el / done) * (total - done)) : null;
    const line = `[${args.variant}] ${done}/${total} done (${ok} ok, ${fail} failed), `
      + `${Math.round(el)}s elapsed` + (eta != null ? `, ~${eta}s left` : '');
    eprint(line);
    try { writeFileNoFollow(join(vdir, 'progress.txt'), line + '\n'); } catch {}
  };
  const tick = setInterval(progress, 30_000);
  workersStarted = true;
  await Promise.all(Array.from({ length: Math.max(1, args.concurrency) }, worker));
  clearInterval(tick); progress();
  eprint(`[${args.variant}] done - ${ok} ok, ${fail} failed -> ${resultsPath}`);
  process.exit(fail ? 1 : 0);
}

// Anything main() throws prints as one sanitized line, not a raw stack. Before
// the workers start it is a refusal (a planted link at _state.json or
// results.jsonl, an lstat that fails, an error from loadCases) and exits 2 like
// the preflight refusals. After they start, only a failed errors.jsonl append
// gets here; rows may already be on disk, so say that and exit 1.
let workersStarted = false;
main().catch(e => {
  const m = String(e?.message || e);
  if (workersStarted) { eprint('stopped mid-run (rows already written are kept; re-run to resume): ' + m); process.exit(1); }
  eprint(m.startsWith('refusing to ') ? m : 'refusing to run: ' + m);
  process.exit(2);
});

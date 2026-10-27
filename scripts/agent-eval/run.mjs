#!/usr/bin/env node
// run.mjs <agent> [--cases id1,id2] [--kind real,planted] [--limit N] [--reps N] [--model m] [--budget usd] [--out dir]
//
// Sandboxed live runner for improve-agent-benchmarks. One `claude -p --agent <agent>` session per
// case (and rep) on the agent's production model, then a trace record that grade.py scores.
//
// Isolation, per run (lessons from evals/persona-accuracy/run-eval.mjs):
//   - temp working dir holding the frozen fixture files (prompt paths are rewritten into it); the run
//     starts in the spawn's original root mapped into that dir, so relative refs still resolve
//   - user settings, hooks and plugins off: --setting-sources project,local, plus --strict-mcp-config
//     with no servers and --disable-slash-commands. The only hook is shims/pretool_hook.py.
//   - DISABLE_AUTOUPDATER=1, --no-session-persistence (runs never become harvestable history)
//   - gh, log-learning.py and external writes are shims (shims/); every call lands in the shim log.
//     A temp learnings file stands in for unified-learnings.jsonl. The real file is sha256-checked
//     before and after the run.
//   - a spawned child agent is never run: the hook returns that child's real recorded output from the
//     same session log (child_lookup.py).
//   - agents whose external writes cannot be faked (MCP write tools) are marked history-only: no live runs.
//
// Output (gitignored): <bench>/<agent>/traces/<case_id>_rep<k>.json   (grade.py records)
//   <bench>/<agent>/errors.jsonl   harness errors and usage-limit notices - never in traces, so never in pass rates
//   <bench>/<agent>/runs/<ts>.json run summary incl. real-learnings sha256 before/after
// --out <dir> writes traces there instead of <bench>/<agent>/traces (iterate runs keep baselines apart).
// The agent file is the repo's claude-code-shared/agents copy (AGENT_BENCH_AGENT_FILE overrides), so an
// uncommitted fix is what gets tested; shell calls to ~/.dotfiles/claude-code-shared/scripts/* are
// rewritten to this repo's scripts/ by the hook (SHIM_SCRIPTS_DIR).
// Paths honour AGENT_BENCH_DIR / AGENT_EVALS_DIR like bench_lib.py. AGENT_BENCH_CONFIG_DIR picks the
// claude config dir (default: first of ~/.cch, ~/.cco that has the agent). CLAUDE_BIN overrides the binary.
//
//   node claude-code-shared/scripts/agent-eval/run.mjs artifact-grounding-judge --limit 6
//   python3 claude-code-shared/scripts/agent-eval/grade.py <contract.json> .claude/agent-bench/<agent>/traces

import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHARED = dirname(dirname(HERE));
const HOME = homedir();
const CLAUDE_BIN = process.env.CLAUDE_BIN || join(HOME, '.local/share/fnm/node-versions/v24.19.0/installation/bin/claude');
const MCP_WRITE = /(create|update|delete|remove|post|send|write|add|save|publish|merge|comment|upload|insert)/i;
const USAGE_LIMIT = /(you've hit your .*limit|usage limit|session limit|weekly limit|limit reached)/i;

function die(msg, code = 2) { console.error(msg); process.exit(code); }
const repoRoot = () => execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: HERE, encoding: 'utf8' }).trim();
const benchDir = () => process.env.AGENT_BENCH_DIR || join(repoRoot(), '.claude', 'agent-bench');
const evalsDir = () => process.env.AGENT_EVALS_DIR || join(SHARED, 'evals');
const sha = p => (existsSync(p) ? createHash('sha256').update(readFileSync(p)).digest('hex') : 'absent');
const now = () => new Date().toISOString();

function parseArgs(argv) {
  const a = { agent: null, cases: null, kinds: ['real', 'planted'], limit: Infinity, reps: 1, model: null, budget: '1.5', timeoutS: 420, out: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i] ?? die(`${k} needs a value`);
    if (k === '--cases') a.cases = new Set(v().split(','));
    else if (k === '--kind') a.kinds = v().split(',');
    else if (k === '--limit') a.limit = Number(v());
    else if (k === '--reps') a.reps = Number(v());
    else if (k === '--model') a.model = v();
    else if (k === '--budget') a.budget = v();
    else if (k === '--out') a.out = v();
    else if (k === '--timeout-s') a.timeoutS = Number(v());
    else if (k.startsWith('--')) die(`unknown flag ${k}`);
    else if (!a.agent) a.agent = k;
    else die(`unexpected argument ${k}`);
  }
  if (!a.agent) die('usage: run.mjs <agent> [--cases ids] [--kind real,planted] [--limit N] [--reps N] [--model m] [--budget usd] [--out dir]');
  return a;
}

// --- agent definition and model -------------------------------------------------------------

function configDirFor(agent) {
  const cands = process.env.AGENT_BENCH_CONFIG_DIR ? [process.env.AGENT_BENCH_CONFIG_DIR] : [join(HOME, '.cch'), join(HOME, '.cco')];
  for (const d of cands) if (findAgentFile(join(d, 'agents'), agent)) return d;
  die(`agent '${agent}' not found under ${cands.map(d => join(d, 'agents')).join(' or ')}`);
}

function findAgentFile(dir, agent) {
  if (!existsSync(dir)) return null;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory() || (e.isSymbolicLink() && statSync(p).isDirectory())) { const r = findAgentFile(p, agent); if (r) return r; }
    else if (e.name === `${agent}.md`) return p;
  }
  return null;
}

function frontmatter(file) {
  const m = readFileSync(file, 'utf8').match(/^---\n([\s\S]*?)\n---/);
  const fm = {};
  for (const ln of (m ? m[1] : '').split('\n')) { const mm = ln.match(/^(\w[\w-]*):\s*(.*)$/); if (mm) fm[mm[1]] = mm[2].trim(); }
  const body = readFileSync(file, 'utf8').replace(/^---\n[\s\S]*?\n---\n?/, '').trim();
  return { tools: (fm.tools || '').split(',').map(s => s.trim()).filter(Boolean), model: fm.model || null, description: fm.description || file, body };
}

// Production model: model-tiers.json agents map -> tier model; an unlisted agent runs on the model in
// its own frontmatter (what a real spawn uses); otherwise the file's default tier.
function productionModel(agent, fm) {
  const t = JSON.parse(readFileSync(join(SHARED, 'resources', 'model-tiers.json'), 'utf8'));
  const tier = t.agents?.[agent];
  if (tier && t.tiers[tier]) return { model: t.tiers[tier].model, effort: t.tiers[tier].effort, via: `model-tiers.json agents.${agent}=${tier}` };
  if (fm.model) return { model: fm.model, effort: null, via: 'agent frontmatter (agent not in model-tiers.json)' };
  const d = t.tiers[t.default];
  return { model: d.model, effort: d.effort, via: `model-tiers.json default ${t.default}` };
}

// --- cases ------------------------------------------------------------------------------------

function loadCases(agent) {
  const p = join(evalsDir(), agent, 'cases.jsonl');
  if (!existsSync(p)) die(`no cases at ${p}`);
  return readFileSync(p, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l));
}

const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// The directory the original spawn ran in. Relative paths in a prompt (e.g. evidence refs like
// clients/web/x.tsx) resolve against it, so the sandbox must run from the same spot. c.input.cwd wins;
// otherwise a relative path in the prompt that is the tail of a frozen file's absolute path gives that
// root (most votes across files). Null when the prompt only uses absolute paths.
function spawnRoot(c, prompt) {
  if (c.input.cwd) return c.input.cwd;
  const votes = new Map();
  for (const f of c.input.files || []) {
    const parts = f.path.split('/');
    for (let i = 2; i < parts.length - 1; i++) {  // longest tail first; tails keep at least one dir
      const tail = parts.slice(i).join('/');
      if (new RegExp(`(^|[^\\w./~-])${reEscape(tail)}`).test(prompt)) {
        const root = parts.slice(0, i).join('/');
        votes.set(root, (votes.get(root) || 0) + 1);
        break;
      }
    }
  }
  return [...votes].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

// Copy the frozen files into the sandbox and rewrite the recorded absolute paths in the prompt.
// Returns the prompt and the dir to run in (the spawn's root mapped into the sandbox, else the box).
function stageCase(c, agent, box) {
  const fx = join(evalsDir(), agent, 'fixtures', c.id);
  const promptFile = join(evalsDir(), agent, c.input.prompt_file);
  if (!existsSync(promptFile)) throw Object.assign(new Error(`fixture missing: ${promptFile} (rebuild with cases_freeze.py)`), { failure_class: 'harness' });
  let prompt = readFileSync(promptFile, 'utf8');
  const root = spawnRoot(c, prompt);
  const cwd = root ? join(box, 'files', root.replace(/^\/+/, '')) : box;
  mkdirSync(cwd, { recursive: true });
  for (const f of c.input.files || []) {
    const src = join(fx, f.fixture);
    if (!existsSync(src)) throw Object.assign(new Error(`fixture file missing: ${src}`), { failure_class: 'harness' });
    const dst = join(box, 'files', f.path.replace(/^\/+/, ''));
    mkdirSync(dirname(dst), { recursive: true });
    copyFileSync(src, dst);
    prompt = prompt.split(f.path).join(dst);
    if (f.path.startsWith(HOME + '/')) prompt = prompt.split('~' + f.path.slice(HOME.length)).join(dst);
  }
  return { prompt, cwd, root };
}

// --- one run ------------------------------------------------------------------------------------

function runCli(args, opts, timeoutMs) {
  return new Promise((res, rej) => {
    const p = spawn(CLAUDE_BIN, args, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '', timedOut = false;
    const timer = setTimeout(() => { timedOut = true; p.kill('SIGKILL'); }, timeoutMs);
    p.stdout.on('data', d => { out += d; });
    p.stderr.on('data', d => { err += d; });
    p.on('error', e => { clearTimeout(timer); rej(e); });
    p.on('close', code => { clearTimeout(timer); res({ code, out, err, timedOut }); });
  });
}

const resultText = c => (Array.isArray(c) ? c.map(x => x.text ?? '').join('') : typeof c === 'string' ? c : JSON.stringify(c ?? ''));

async function runOne({ c, rep, agent, fm, model, cfg, args, hasAgentTool }) {
  const box = mkdtempSync(join(tmpdir(), 'agent-bench-'));
  const shimLog = join(box, 'shim-log.jsonl');
  const learnings = join(box, 'learnings', 'unified-learnings.jsonl');
  const childrenFile = join(box, 'children.json');
  mkdirSync(join(box, 'learnings'), { recursive: true });
  writeFileSync(shimLog, '');
  try {
    const { prompt, cwd, root } = stageCase(c, agent, box);
    if (hasAgentTool && c.source?.session_id) {
      execFileSync('python3', [join(HERE, 'child_lookup.py'), c.source.session_id, c.source.agent_id, agent, childrenFile],
        { cwd: HERE, env: process.env, encoding: 'utf8' });
    }
    const hook = `python3 ${join(HERE, 'shims', 'pretool_hook.py')}`;
    const settings = { hooks: { PreToolUse: [{ matcher: 'Bash|Agent|Task', hooks: [{ type: 'command', command: hook }] }] } };
    // --setting-sources drops the user dir, which is also where --agent looks, so the production agent
    // file (same body, tools, model) is handed over inline.
    const defs = { [agent]: { description: fm.description, prompt: fm.body, tools: fm.tools, model: model.model } };
    const cli = ['-p', prompt, '--agent', agent, '--agents', JSON.stringify(defs), '--output-format', 'stream-json', '--verbose',
      '--model', model.model, ...(model.effort && /^(low|medium|high|xhigh|max)$/.test(model.effort) ? ['--effort', model.effort] : []),
      '--allowedTools', fm.tools.join(','),
      '--settings', JSON.stringify(settings), '--setting-sources', 'project,local',
      '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands',
      '--no-session-persistence', '--max-budget-usd', args.budget];
    const env = {
      ...process.env, CLAUDE_CONFIG_DIR: cfg, DISABLE_AUTOUPDATER: '1',
      PATH: `${join(HERE, 'shims')}:${process.env.PATH}`,
      SHIM_LOG: shimLog, SHIM_LEARNINGS: learnings, SHIM_CHILDREN: childrenFile,
      SHIM_SCRIPTS_DIR: join(SHARED, 'scripts'),
      LOG_LEARNING_DEST: join(box, 'learnings'),
    };
    const r = await runCli(cli, { cwd, env }, args.timeoutS * 1000);
    if (r.timedOut) throw Object.assign(new Error(`timeout after ${args.timeoutS}s`), { failure_class: 'harness' });
    const events = r.out.split('\n').filter(Boolean).flatMap(l => { try { return [JSON.parse(l)]; } catch { return []; } });
    const result = events.findLast(e => e.type === 'result');
    const init = events.find(e => e.type === 'system' && e.subtype === 'init');
    if (!result) throw Object.assign(new Error(`no result event (exit ${r.code}): ${r.err.slice(0, 300)}`), { failure_class: 'harness' });

    const calls = [], results = new Map(), texts = [];
    for (const ev of events) {
      const content = ev.message?.content;
      if (!Array.isArray(content)) continue;
      if (ev.type === 'assistant') for (const b of content) {
        if (b.type === 'tool_use') calls.push({ id: b.id, name: b.name, input: b.input });
        else if (b.type === 'text') texts.push(b.text);
      } else if (ev.type === 'user') for (const b of content) {
        if (b.type === 'tool_result') results.set(b.tool_use_id, [resultText(b.content), !!b.is_error]);
      }
    }
    for (const k of calls) { const x = results.get(k.id); [k.result, k.is_error] = x ?? [null, false]; }
    const final = (result.result ?? texts.at(-1) ?? '').toString();
    if (USAGE_LIMIT.test(final.trim().slice(0, 300)) && !calls.length) {
      throw Object.assign(new Error(`usage limit: ${final.trim().slice(0, 200)}`), { failure_class: 'usage_limit' });
    }
    if (result.is_error) throw Object.assign(new Error(`cli error ${result.subtype}: ${final.slice(0, 300)}`), { failure_class: 'harness' });
    const mu = result.modelUsage || {};
    const billed = Object.keys(mu).sort((a, b) => (mu[b].costUSD || 0) - (mu[a].costUSD || 0))[0];
    if (!billed) throw Object.assign(new Error(`no model billed (exit ${r.code}): ${final.slice(0, 200)}`), { failure_class: 'harness' });

    const shimCalls = readFileSync(shimLog, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l));
    const filesWritten = [];
    for (const k of calls) {
      if (['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(k.name)) {
        const p = k.input?.file_path || k.input?.notebook_path;
        if (p && !filesWritten.includes(p)) filesWritten.push(p);
      }
    }
    for (const s of shimCalls) for (const p of s.writes || []) if (!filesWritten.includes(p)) filesWritten.push(p);
    const u = result.usage || {};
    return {
      agent, agent_id: `${c.id}_rep${rep}`, case_id: c.id, rep, kind: c.kind, source: 'live', timestamp: now(),
      model: billed, models_used: Object.keys(mu), spawn_prompt: prompt, final_output: final, tool_calls: calls,
      files_written: filesWritten, expected: c.expected, shim_calls: shimCalls,
      usage: { input: u.input_tokens || 0, output: u.output_tokens || 0, cache_read: u.cache_read_input_tokens || 0, cache_creation: u.cache_creation_input_tokens || 0 },
      cost_usd: result.total_cost_usd, num_turns: result.num_turns, spawn_root: root,
      session_init: init ? { tools: init.tools, mcp_servers: init.mcp_servers, plugins: init.plugins, agents: init.agents, model: init.model, config_dir: cfg } : null,
      temp_learnings_lines: existsSync(learnings) ? readFileSync(learnings, 'utf8').split('\n').filter(Boolean).length : 0,
    };
  } finally {
    rmSync(box, { recursive: true, force: true });
  }
}

// --- main ---------------------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const agent = args.agent;
  const cfg = configDirFor(agent);
  const agentFile = process.env.AGENT_BENCH_AGENT_FILE || findAgentFile(join(SHARED, 'agents'), agent) || findAgentFile(join(cfg, 'agents'), agent);
  const fm = frontmatter(agentFile);
  const bench = join(benchDir(), agent);
  const tracesDir = args.out || join(bench, 'traces');
  mkdirSync(tracesDir, { recursive: true });
  mkdirSync(join(bench, 'runs'), { recursive: true });

  // History-only: external writes that cannot be faked safely. No live runs.
  const mcpWrites = fm.tools.filter(t => t.startsWith('mcp__') && MCP_WRITE.test(t));
  if (mcpWrites.length) {
    const reason = `external MCP write tools cannot be shimmed: ${mcpWrites.join(', ')}`;
    writeFileSync(join(bench, 'history-only.json'), JSON.stringify({ agent, reason, ts: now() }, null, 2) + '\n');
    console.log(`history-only: ${agent} - ${reason}. No live runs; grade history records with grade.py.`);
    return;
  }
  const model = args.model ? { model: args.model, effort: null, via: '--model' } : productionModel(agent, fm);
  const hasAgentTool = fm.tools.includes('Agent') || fm.tools.includes('Task');

  let cases = loadCases(agent).filter(c => args.kinds.includes(c.kind) && (!args.cases || args.cases.has(c.id)));
  cases = cases.slice(0, args.limit);
  if (!cases.length) die('no runnable cases selected (live runs support kinds with a frozen prompt: real, planted)');

  const realLearnings = [join(SHARED, 'learnings', 'unified-learnings.jsonl'), join(HOME, '.dotfiles', 'claude-code-shared', 'learnings', 'unified-learnings.jsonl')];
  const before = Object.fromEntries(realLearnings.map(p => [p, sha(p)]));
  console.log(`agent=${agent} model=${model.model} (${model.via}) cases=${cases.length} reps=${args.reps} config=${cfg}`);

  const summary = { agent, started: now(), model, cases: [], errors: 0, stopped: null };
  outer: for (const c of cases) {
    for (let rep = 1; rep <= args.reps; rep++) {
      const tracePath = join(tracesDir, `${c.id}_rep${rep}.json`);
      try {
        const rec = await runOne({ c, rep, agent, fm, model, cfg, args, hasAgentTool });
        writeFileSync(tracePath, JSON.stringify(rec, null, 1));
        summary.cases.push({ id: c.id, rep, cost_usd: rec.cost_usd, shim_calls: rec.shim_calls.length });
        console.log(`ok   ${c.id} rep${rep} $${(rec.cost_usd ?? 0).toFixed(4)} shim_calls=${rec.shim_calls.length} -> ${tracePath}`);
      } catch (e) {
        summary.errors++;
        const row = { ts: now(), case_id: c.id, rep, failure_class: e.failure_class || 'harness', message: String(e.message).slice(0, 600) };
        appendFileSync(join(bench, 'errors.jsonl'), JSON.stringify(row) + '\n');
        console.log(`ERR  ${c.id} rep${rep} [${row.failure_class}] ${row.message.slice(0, 160)} -> errors.jsonl (excluded from pass rates)`);
        if (row.failure_class === 'usage_limit') { summary.stopped = 'usage_limit'; break outer; }
      }
    }
  }
  const after = Object.fromEntries(realLearnings.map(p => [p, sha(p)]));
  summary.real_learnings = Object.fromEntries(realLearnings.map(p => [p, { before: before[p], after: after[p], unchanged: before[p] === after[p] }]));
  summary.ended = now();
  writeFileSync(join(bench, 'runs', `${summary.started.replace(/[:.]/g, '-')}.json`), JSON.stringify(summary, null, 2) + '\n');
  for (const p of realLearnings) console.log(`real learnings ${before[p] === after[p] ? 'UNCHANGED' : 'CHANGED!!'} ${p} sha256 ${before[p].slice(0, 16)} -> ${after[p].slice(0, 16)}`);
  if (Object.values(summary.real_learnings).some(x => !x.unchanged)) process.exit(4);
}

main().catch(e => { console.error(e); process.exit(1); });

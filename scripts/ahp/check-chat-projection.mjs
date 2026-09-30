// Check the Python projection fixtures against the actual pinned AHP reducer.
// AHP_SOURCE must be a scratch upstream checkout with its compiler installed.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, readdir, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const source = path.resolve(process.env.AHP_SOURCE ?? '../calliope-vscode/vscode');
const ts = createRequire(path.join(source, 'package.json'))('typescript');
const root = path.join(source, 'src/vs/platform/agentHost/common/state/protocol');
const target = await mkdtemp(path.join(tmpdir(), 'ahp-reducer-'));
async function compile(directory, relative = '') {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const rel = path.join(relative, entry.name);
    if (entry.isDirectory()) await compile(path.join(directory, entry.name), rel);
    else if (entry.name.endsWith('.ts')) {
      const destination = path.join(target, rel.replace(/\.ts$/, '.js'));
      await mkdir(path.dirname(destination), { recursive: true });
      const text = await readFile(path.join(directory, entry.name), 'utf8');
      await writeFile(destination, ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText);
    }
  }
}
try {
  await writeFile(path.join(target, 'package.json'), '{"type":"module"}');
  await compile(root);
  const { chatReducer } = await import(pathToFileURL(path.join(target, 'channels-chat/reducer.js')));
  const file = path.resolve('backend/astrolift_agents/tests/fixtures/ahp-chat.json');
  const fixture = JSON.parse(await readFile(file, 'utf8'));
  assert.equal(execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim(), fixture.upstreamCommit, 'upstream pin differs');
  assert.equal(fixture.protocolVersion, '1.0.0');
  for (const scenario of fixture.scenarios) {
    let state = scenario.initial;
    for (const step of scenario.steps) {
      state = JSON.parse(JSON.stringify(chatReducer(state, step.action)));
      if (process.argv.includes('--update')) step.expected = state;
      else assert.deepEqual(state, step.expected, scenario.name + ': ' + step.action.type);
    }
  }
  if (process.argv.includes('--update')) await writeFile(file, JSON.stringify(fixture, null, 2) + '\n');
  console.log(`Pinned upstream reducer agrees with ${fixture.scenarios.length} projection scenarios`);
} finally { await rm(target, { recursive: true, force: true }); }

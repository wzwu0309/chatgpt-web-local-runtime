#!/usr/bin/env node
// exposure golden-path —— ChatGPT 绑定面回归 + Generic 显式 workspace 面验证
// 用法: node exposure-golden-path.mjs [--base URL] [--state-file FILE] [--out FILE]
//   --base        bridge 端点前缀,默认 http://127.0.0.1:3081/api/chatgpt-bridge
//   --state-file  session-workspaces.json(用于验证 generic 面绑定表零写入)
//   --out         报告输出路径,默认 stdout
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

const args = process.argv.slice(2)
function opt(name, dflt) {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : dflt
}
const BASE = opt('base', 'http://127.0.0.1:3081/api/chatgpt-bridge')
const STATE_FILE = opt('state-file', null)
const OUT = opt('out', null)
const CHATGPT_SESSION = 'exposure-golden-chatgpt'
const GENERIC_SESSION = 'exposure-golden-generic'

const report = { base: BASE, checks: {}, failures: [] }
function check(name, ok, details = {}) {
  report.checks[name] = { ok: Boolean(ok), ...details }
  if (!ok) report.failures.push(name)
}
async function api(path, payload) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  })
  const text = await res.text()
  let body
  try { body = JSON.parse(text) } catch { body = { _unparseable: text.slice(0, 200) } }
  return { status: res.status, body }
}
function textOf(result) {
  const blocks = result?.body?.content
  if (!Array.isArray(blocks)) return ''
  return blocks.filter(block => block?.type === 'text').map(block => block.text).join('\n')
}
function toolIsError(result) {
  return result.status !== 200 || result.body?.isError === true
}
async function callTool(name, argsObj, envelope = {}) {
  return api('/call', { name, arguments: argsObj, ...envelope })
}
async function pwdProbe(envelope) {
  const result = await callTool('bash', { command: 'pwd', description: 'exposure workspace probe' }, envelope)
  return { result, cwd: result.body?.value?.cwd ?? textOf(result).trim() }
}
function readSessions() {
  if (!STATE_FILE || !existsSync(STATE_FILE)) return null
  try { return JSON.parse(readFileSync(STATE_FILE, 'utf8'))?.sessions ?? {} } catch { return null }
}

const revision = await api('/revision', {})
if (revision.status !== 200) {
  console.error(`revision probe failed: HTTP ${revision.status}`)
  process.exit(1)
}
const root = realpathSync(revision.body.workspace)
const probeId = randomUUID().slice(0, 8)
const projectA = join(root, `.exposure-golden-${probeId}-project-a`)
const projectB = join(root, `.exposure-golden-${probeId}-project-b`)
mkdirSync(projectA, { recursive: true })
mkdirSync(projectB, { recursive: true })
const realA = realpathSync(projectA)
const realB = realpathSync(projectB)
report.workspaceRoot = root

const bindingCatalog = await api('/tools', { binding: true })
const genericCatalog = await api('/tools', { binding: false })
const namesOf = response => new Set((response.body?.tools ?? []).map(tool => tool.name))
const bindingNames = namesOf(bindingCatalog)
const genericNames = namesOf(genericCatalog)
const bindingDependent = ['workspace_switch', 'workspace_status', 'task_state']
check('chatgpt_catalog_keeps_binding_suite', bindingDependent.every(name => bindingNames.has(name)), {
  missing: bindingDependent.filter(name => !bindingNames.has(name)),
})
check('generic_catalog_filters_binding_suite', bindingDependent.every(name => !genericNames.has(name)), {
  leaked: bindingDependent.filter(name => genericNames.has(name)),
})

const missingIdentity = await callTool('workspace_switch', { path: realA }, { binding: true })
check('chatgpt_switch_without_identity_is_loud', toolIsError(missingIdentity) && textOf(missingIdentity).includes('session identity missing'), {
  status: missingIdentity.status,
  text: textOf(missingIdentity).slice(0, 180),
})

const switchToA = await callTool('workspace_switch', { path: realA }, { sessionId: CHATGPT_SESSION, binding: true })
check('chatgpt_switch_with_identity_succeeds', !toolIsError(switchToA) && switchToA.body?.value?.workspace === realA, {
  status: switchToA.status,
  workspace: switchToA.body?.value?.workspace,
})
const boundPwd = await pwdProbe({ sessionId: CHATGPT_SESSION, binding: true })
check('chatgpt_binding_routes_following_calls', boundPwd.cwd === realA, { cwd: boundPwd.cwd, expected: realA })

const overridePwd = await pwdProbe({ sessionId: CHATGPT_SESSION, binding: true, workspace: realB })
check('explicit_workspace_overrides_session_binding', overridePwd.cwd === realB, { cwd: overridePwd.cwd, expected: realB })

const genericA = await pwdProbe({ sessionId: GENERIC_SESSION, binding: false, workspace: realA })
check('generic_explicit_workspace_routes_to_project_a', genericA.cwd === realA, { cwd: genericA.cwd, expected: realA })
const genericB = await pwdProbe({ sessionId: GENERIC_SESSION, binding: false, workspace: realB })
check('generic_workspace_sequence_is_per_call_consistent', genericB.cwd === realB, { cwd: genericB.cwd, expected: realB })
const genericDefault = await pwdProbe({ sessionId: GENERIC_SESSION, binding: false })
check('generic_missing_workspace_falls_back_to_launch_root', genericDefault.cwd === root, { cwd: genericDefault.cwd, expected: root })
const genericIgnoresBinding = await pwdProbe({ sessionId: CHATGPT_SESSION, binding: false })
check('generic_non_participation_ignores_existing_binding', genericIgnoresBinding.cwd === root, {
  cwd: genericIgnoresBinding.cwd,
  expected: root,
  boundWorkspace: realA,
})

const outside = await pwdProbe({ sessionId: GENERIC_SESSION, binding: false, workspace: '/' })
check('generic_out_of_bounds_workspace_is_rejected', outside.result.status === 400 && outside.result.body?.error === 'invalid_workspace', {
  status: outside.result.status,
  error: outside.result.body?.error,
})

const genericSwitch = await callTool('workspace_switch', { path: realA }, { sessionId: GENERIC_SESSION, binding: false })
check('generic_binding_dependent_tool_is_rejected', genericSwitch.status === 400 && genericSwitch.body?.error === 'tool_unavailable', {
  status: genericSwitch.status,
  error: genericSwitch.body?.error,
})

if (STATE_FILE) {
  const sessions = readSessions()
  check('generic_binding_table_zero_writes', sessions !== null && !(GENERIC_SESSION in sessions), {
    stateFile: STATE_FILE,
    genericSessionPresent: sessions ? GENERIC_SESSION in sessions : null,
  })
  check('binding_state_probe_is_meaningful', sessions !== null && CHATGPT_SESSION in sessions, {
    chatgptSessionPresent: sessions ? CHATGPT_SESSION in sessions : null,
  })
} else {
  report.checks.generic_binding_table_zero_writes = { skip: 'no --state-file supplied' }
}

// 清理:让 ChatGPT probe 会话回到启动根,并移除本次唯一 probe 目录。
await callTool('workspace_switch', { path: root }, { sessionId: CHATGPT_SESSION, binding: true }).catch(() => {})
rmSync(projectA, { recursive: true, force: true })
rmSync(projectB, { recursive: true, force: true })

report.ok = report.failures.length === 0
const json = JSON.stringify(report, null, 2)
if (OUT) {
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, `${json}\n`)
  console.log(`written: ${OUT}`)
} else {
  console.log(json)
}
if (!report.ok) {
  console.error(`failed checks: ${report.failures.join(', ')}`)
  process.exit(1)
}

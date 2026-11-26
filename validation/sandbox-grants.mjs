#!/usr/bin/env node
// sandbox-grants golden-path — 验证部署级 additionalWritableRoots 在真实
// catalog/call 边界生效。用法:
//   node sandbox-grants.mjs --root /abs/one --root /abs/two [--base URL] [--out FILE]
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { homedir } from 'node:os'

const args = process.argv.slice(2)
function opt(name, dflt) {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : dflt
}
function repeated(name) {
  const out = []
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === `--${name}`) out.push(args[i + 1])
  }
  return out
}

const BASE = opt('base', 'http://127.0.0.1:3081/api/chatgpt-bridge')
const OUT = opt('out', null)
const ROOTS = repeated('root')
const report = { base: BASE, roots: ROOTS, checks: {}, failures: [] }
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
function isError(result) {
  return result.status !== 200 || result.body?.isError === true
}
function shQuote(value) {
  return `'${String(value).replaceAll(`'`, `'\\''`)}'`
}
async function callTool(name, argsObj) {
  return api('/call', { name, arguments: argsObj })
}

if (ROOTS.length === 0) {
  console.error('at least one --root is required')
  process.exit(2)
}
for (const root of ROOTS) {
  if (!isAbsolute(root)) {
    console.error(`--root must be absolute: ${root}`)
    process.exit(2)
  }
}

const revision = await api('/revision', {})
if (revision.status !== 200) {
  console.error(`revision probe failed: HTTP ${revision.status}`)
  process.exit(1)
}
const catalog = await api('/tools', {})
const toolNames = (catalog.body?.tools ?? []).map(tool => tool?.name ?? tool?.function?.name).filter(Boolean)
const has = name => toolNames.includes(name)
const policyRoots = revision.body?.sandboxPolicy?.additionalWritableRoots ?? []
const probeId = randomUUID().slice(0, 8)
const createdDirs = []
const negativePath = join(homedir(), `.sandbox-grants-evil-${probeId}`)

for (const configured of ROOTS) {
  const label = configured.replaceAll(/[^A-Za-z0-9]+/g, '_').replaceAll(/^_+|_+$/g, '')
  let real
  try {
    real = realpathSync(configured)
  } catch (error) {
    check(`${label}:configured_root_exists`, false, { error: String(error) })
    continue
  }
  check(`${label}:revision_reports_additional_root`, policyRoots.includes(real), { expected: real, policyRoots })
  if (!existsSync(real)) continue

  const probeDir = join(real, `.dsh-sandbox-grant-probe-${probeId}`)
  mkdirSync(probeDir, { recursive: true })
  createdDirs.push(probeDir)

  if (has('write')) {
    const target = join(probeDir, 'file-tool-write.txt')
    const result = await callTool('write', { file_path: target, content: `sandbox-grant-${probeId}\n` })
    check(`${label}:file_tool_write_uses_additional_root`, !isError(result), {
      status: result.status,
      text: textOf(result).slice(0, 300),
    })
  } else {
    report.checks[`${label}:file_tool_write_uses_additional_root`] = { skip: 'write not in catalog' }
  }

  if (has('bash')) {
    const target = join(probeDir, 'bash-write.txt')
    const command = `if printf '%s\\n' ${shQuote(`sandbox-grant-${probeId}`)} > ${shQuote(target)} 2>/dev/null; then echo grant-ok; else echo grant-denied; exit 43; fi`
    const result = await callTool('bash', { command, description: 'sandbox additional writable root probe' })
    const text = textOf(result)
    check(`${label}:bash_write_uses_additional_root`, !isError(result) && text.includes('grant-ok'), {
      status: result.status,
      text: text.slice(0, 300),
    })
  } else {
    report.checks[`${label}:bash_write_uses_additional_root`] = { skip: 'bash not in catalog (Windows process grants intentionally differ)' }
  }
}

if (has('bash')) {
  const command = `if printf x > ${shQuote(negativePath)} 2>/dev/null; then echo unexpected-write; rm -f ${shQuote(negativePath)}; exit 44; else echo denied-ok; fi`
  const result = await callTool('bash', { command, description: 'sandbox negative control probe' })
  const text = textOf(result)
  check('home_root_remains_denied', !isError(result) && text.includes('denied-ok') && !text.includes('unexpected-write'), {
    status: result.status,
    text: text.slice(0, 300),
  })
}

for (const dir of createdDirs) rmSync(dir, { recursive: true, force: true })

report.ok = report.failures.length === 0
const json = JSON.stringify(report, null, 2)
if (OUT) writeFileSync(OUT, `${json}\n`)
else console.log(json)
if (!report.ok) {
  console.error(`failed checks: ${report.failures.join(', ')}`)
  process.exit(1)
}

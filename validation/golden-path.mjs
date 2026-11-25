#!/usr/bin/env node
// golden-path 冒烟脚本 —— DSH 升级前后行为对照的脊柱
// 用法: node golden-path.mjs [--base URL] [--out FILE] [--session ID]
//   --base   bridge 端点前缀,默认 http://127.0.0.1:3081/api/chatgpt-bridge
//   --out    报告输出路径,默认 stdout
//   --session  探测会话 id,默认 golden-path-probe(与真实会话永不冲突)
// 设计:catalog 驱动 —— 工具存在才测,缺失记 skip;输出为归一化 JSON(去时间戳/随机 id)
import { writeFileSync, mkdirSync, rmSync, existsSync } from 'node:fs'
import { dirname } from 'node:path'

const args = process.argv.slice(2)
function opt(name, dflt) {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : dflt
}
const BASE = opt('base', 'http://127.0.0.1:3081/api/chatgpt-bridge')
const OUT = opt('out', null)
const SESSION = opt('session', 'golden-path-probe')
const PROBE_DIR_SUFFIX = '.golden-path-probe'

const report = { base: BASE, session: SESSION, probes: {}, catalog: null, errors: [] }

async function api(path, body) {
  const res = await fetch(`${BASE}${path}`, body !== undefined
    ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...body, sessionId: SESSION }) }
    : undefined)
  const text = await res.text()
  let json
  try { json = JSON.parse(text) } catch { json = { _unparseable: text.slice(0, 200) } }
  return { status: res.status, body: json }
}

async function callTool(name, argsObj) {
  const r = await api('/call', { name, arguments: argsObj })
  return r
}

// 从工具结果 content 提取文本(归一化:去路径差异的统一占位由调用方处理)
function textOf(result) {
  const blocks = result?.body?.content
  if (!Array.isArray(blocks)) return null
  return blocks.filter(b => b?.type === 'text').map(b => b.text).join('\n')
}
function isError(result) {
  return result?.body?.isError === true || result?.status !== 200
}

// ── 1. revision 健康探针 ──
try {
  const r = await api('/revision')
  report.probes.revision = {
    status: r.status,
    capabilityProfile: r.body?.capabilityProfile ?? null,
    sandboxMode: r.body?.sandboxPolicy?.mode ?? null,
    toolRevisionType: typeof r.body?.toolRevision,
    workspace: r.body?.workspace ?? null,
  }
} catch (e) { report.probes.revision = { fatal: e.message } }

// ── 2. 工具目录快照(原样存档供 diff) ──
let toolNames = []
try {
  const r = await api('/tools')
  report.catalog = r.body?.tools ?? null
  toolNames = (r.body?.tools ?? []).map(t => t?.name ?? t?.function?.name).filter(Boolean)
  report.probes.catalog = { status: r.status, count: toolNames.length, names: toolNames.sort() }
} catch (e) { report.probes.catalog = { fatal: e.message } }

const has = n => toolNames.includes(n)
const WS = report.probes.revision?.workspace
const probeDir = WS ? `${WS}/${PROBE_DIR_SUFFIX}` : null

// ── 3. bash echo ──
if (has('bash')) {
  const r = await callTool('bash', { command: 'echo golden-$((40+2))', description: 'golden path probe' })
  report.probes.bash_echo = {
    error: isError(r),
    text: (textOf(r) ?? '').replace(/\/private\/tmp|\/tmp|\/var\/folders\/\S+/g, '<TMP>').trim().slice(0, 200),
  }
} else report.probes.bash_echo = { skip: 'bash not in catalog' }

// ── 4. write+read 往返(在工作区内 probe 目录) ──
if (has('write') && has('read') && probeDir) {
  const fp = `${probeDir}/roundtrip.txt`
  await callTool('bash', { command: `mkdir -p '${probeDir}'`, description: 'probe dir' }).catch(() => {})
  const w = await callTool('write', { file_path: fp, content: 'golden-roundtrip-42\n' })
  const rd = await callTool('read', { file_path: fp })
  report.probes.write_read_roundtrip = {
    writeError: isError(w),
    readError: isError(rd),
    readText: (textOf(rd) ?? '').slice(0, 200),
  }
} else report.probes.write_read_roundtrip = { skip: 'write/read missing or no workspace' }

// ── 5. glob ──
if (has('glob') && probeDir) {
  const r = await callTool('glob', { pattern: '*.txt', path: probeDir })
  report.probes.glob = {
    error: isError(r),
    foundRoundtrip: (textOf(r) ?? JSON.stringify(r.body ?? '')).includes('roundtrip.txt'),
  }
} else report.probes.glob = { skip: 'glob not in catalog' }

// ── 6. workspace_switch + cwd 锚定 ──
if (has('workspace_switch') && probeDir) {
  const sw = await callTool('workspace_switch', { path: probeDir })
  const pwd = await callTool('bash', { command: 'pwd', description: 'cwd probe' })
  report.probes.workspace_switch = {
    switchError: isError(sw),
    pwdText: (textOf(pwd) ?? '').trim().slice(0, 200),
    cwdAnchored: (textOf(pwd) ?? '').includes(PROBE_DIR_SUFFIX),
  }
  // 切回原根,避免 probe 会话遗留影响(虽然 probe session 独立,但干净起见)
  if (WS) await callTool('workspace_switch', { path: WS }).catch(() => {})
} else report.probes.workspace_switch = { skip: 'workspace_switch not in catalog' }

// ── 7. 沙箱逃逸探针:写 $HOME 必须被拒 ──
if (has('bash')) {
  const r = await callTool('bash', { command: 'echo x > "$HOME/.golden-path-evil" 2>&1; echo rc:$?; rm -f "$HOME/.golden-path-evil" 2>/dev/null; true', description: 'sandbox escape probe' })
  const t = textOf(r) ?? ''
  report.probes.sandbox_escape = {
    error: isError(r),
    text: t.slice(0, 300),
    denied: /not permitted|denied|rc:[^0]/.test(t),
  }
} else report.probes.sandbox_escape = { skip: 'bash not in catalog' }

// ── 8. shell_session 持久性(open/send/send/close) ──
if (has('shell_session')) {
  const open = await callTool('shell_session', { action: 'open', name: 'golden-probe' })
  const openText = (textOf(open) ?? '') + JSON.stringify(open.body?.value ?? {})
  const sid = open.body?.value?.sessionId ?? open.body?.value?.id ?? null
  if (!sid) {
    report.probes.shell_session = { openError: true, note: 'cannot extract session id', openText: openText.slice(0, 250) }
  } else {
    await callTool('shell_session', { action: 'send', id: sid, text: 'export GOLDEN_VAR=persistence-check-42' })
    await callTool('shell_session', { action: 'send', id: sid, text: 'echo val:$GOLDEN_VAR' })
    const rd = await callTool('shell_session', { action: 'read', id: sid, count: 20 })
    const t = (textOf(rd) ?? '') + JSON.stringify(rd.body?.value ?? '')
    await callTool('shell_session', { action: 'close', id: sid }).catch(() => {})
    report.probes.shell_session = {
      openError: isError(open),
      persistent: t.includes('persistence-check-42'),
      readText: t.slice(0, 250),
    }
  }
} else report.probes.shell_session = { skip: 'shell_session not in catalog' }

// ── 9. task_state 读写(create/checkpoint/get/complete) ──
if (has('task_state')) {
  const c = await callTool('task_state', { action: 'create', title: 'golden-probe', goal: 'probe task', workspace: 'probe' })
  const cText = (textOf(c) ?? '') + JSON.stringify(c.body?.value ?? c.body ?? '')
  const idm = cText.match(/"?(?:id|task)"?\s*[:=]\s*"?([\w.-]{6,})"?/)
  const tid = idm?.[1] ?? null
  if (!tid) {
    report.probes.task_state = { createError: isError(c), note: 'cannot extract task id', createText: cText.slice(0, 250) }
  } else {
    const g1 = await callTool('task_state', { action: 'get', id: tid })
    const g1Text = JSON.stringify(g1.body?.value ?? {}) + (textOf(g1) ?? '')
    const revm = g1Text.match(/"?(?:revision|if_revision)"?\s*[:=]\s*(\d+)/)
    const rev = revm ? Number(revm[1]) : null
    const cp = rev !== null
      ? await callTool('task_state', { action: 'checkpoint', id: tid, if_revision: rev, summary: 'golden-checkpoint-42' })
      : { status: 0, body: { note: 'no revision extracted' } }
    if (rev !== null) await callTool('task_state', { action: 'complete', id: tid, if_revision: rev + 1 }).catch(() => {})
    report.probes.task_state = {
      createError: isError(c),
      getError: isError(g1),
      checkpointError: isError(cp),
      revisionExtracted: rev,
      checkpointText: (textOf(cp) ?? '').slice(0, 150),
    }
  }
} else report.probes.task_state = { skip: 'task_state not in catalog' }

// ── 10. 清理 probe 文件 ──
if (has('bash') && probeDir) {
  await callTool('bash', { command: `rm -rf '${probeDir}'`, description: 'probe cleanup' }).catch(() => {})
}

const json = JSON.stringify(report, null, 2)
if (OUT) {
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, json + '\n')
  console.log(`written: ${OUT}`)
  console.log(`probes: ${Object.entries(report.probes).map(([k, v]) => `${k}:${v.skip ? 'SKIP' : v.fatal ? 'FATAL' : 'done'}`).join(' ')}`)
} else {
  console.log(json)
}

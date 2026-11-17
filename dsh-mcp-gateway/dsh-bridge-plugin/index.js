import { AsyncLocalStorage } from 'node:async_hooks'
import { createHash, randomUUID } from 'node:crypto'
import { realpathSync, statSync } from 'node:fs'
import { mkdir, open, readFile, realpath, rename, stat, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join, resolve, sep } from 'node:path'

import { buildChatGPTCapabilityProfile } from './chatgpt-capability-profile.js'

export const name = 'dsh-chatgpt-bridge'
export const inject = ['webServer', 'tools', 'skills', 'llm', 'agents', 'agentPresets']

const PREFIX = '/api/chatgpt-bridge'
const MAX_BODY_BYTES = 1_000_000
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024
const MAX_MATERIALIZED_IMAGE_BYTES = 12 * 1024 * 1024
const MAX_PRESET_BYTES = 4 * 1024 * 1024
const TOOL_CALL_TIMEOUT_MS = 120_000
const EXTERNAL_PROVIDER = 'chatgpt-web-external'
const EXTERNAL_MODEL = 'chatgpt-web'
const CAPABILITY_SESSION_PREFIX = 'dsh-mcp-gateway-chatgpt-capability'

// Switchable workspace root. The bridge anchors every capability helper to
// activeWorkspace() instead of the launch cwd so ChatGPT can retarget tool
// execution via the workspace_switch tool. Persisted under DSH_HOME so the
// selection survives host restarts; targets are restricted to the allowed
// root — ~/data by default, operator-configurable via config.allowedRoot.
let workspaceAllowedRoot = join(homedir(), 'data')
const WORKSPACE_STATE_FILE = process.env.DSH_CHATGPT_BRIDGE_WORKSPACE_STATE
  || (process.env.DSH_HOME
    ? join(process.env.DSH_HOME, 'storages', 'chatgpt-bridge', 'active-workspace.json')
    : null)

let currentWorkspace = process.cwd()
let workspaceLoaded = null

// Workspace resolution for one /call: the calling session's binding (set by
// workspace_switch) wins; otherwise the shared default (currentWorkspace,
// persisted across restarts). Session bindings are in-memory only — the
// session id is an opaque conversation-scoped token forwarded by the gateway.
const callContext = new AsyncLocalStorage()
const sessionWorkspaces = new Map()
const MAX_SESSION_WORKSPACES = 256

function activeWorkspace() {
  return currentWorkspace
}

function sessionIdOfCall() {
  return callContext.getStore()?.sessionId ?? null
}

function activeWorkspaceFor(sessionId) {
  if (sessionId) {
    const scoped = sessionWorkspaces.get(sessionId)
    if (scoped) return scoped
  }
  return currentWorkspace
}

function callWorkspace() {
  return activeWorkspaceFor(callContext.getStore()?.sessionId)
}

function bindingSource() {
  const sessionId = sessionIdOfCall()
  return sessionId && sessionWorkspaces.has(sessionId) ? 'session' : 'default'
}

function bindSessionWorkspace(sessionId, real) {
  if (sessionWorkspaces.size >= MAX_SESSION_WORKSPACES && !sessionWorkspaces.has(sessionId)) {
    sessionWorkspaces.delete(sessionWorkspaces.keys().next().value)
  }
  sessionWorkspaces.set(sessionId, real)
}

function ensureWorkspaceLoaded() {
  if (!workspaceLoaded) {
    workspaceLoaded = (async () => {
      if (!WORKSPACE_STATE_FILE) return
      try {
        const persisted = JSON.parse(await readFile(WORKSPACE_STATE_FILE, 'utf8'))
        currentWorkspace = await resolveAllowedWorkspace(persisted?.workspace)
      } catch {
        // Missing/invalid state falls back to the launch cwd.
      }
    })()
  }
  return workspaceLoaded
}

async function resolveAllowedWorkspace(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new Error('path must be a non-empty string')
  }
  const expanded = input.trim().replace(/^~(?=$|[\\/])/, homedir())
  const real = await realpath(resolve(expanded)).catch(() => {
    throw new Error(`path does not exist: ${expanded}`)
  })
  const root = await realpath(workspaceAllowedRoot)
  if (real !== root && !real.startsWith(root + sep)) {
    throw new Error(`path must resolve inside ${workspaceAllowedRoot}: ${real}`)
  }
  if (!(await stat(real)).isDirectory()) {
    throw new Error(`not a directory: ${real}`)
  }
  return real
}

async function setWorkspace(real) {
  if (WORKSPACE_STATE_FILE) {
    await mkdir(dirname(WORKSPACE_STATE_FILE), { recursive: true })
    const tmp = `${WORKSPACE_STATE_FILE}.tmp-${process.pid}`
    await writeFile(tmp, `${JSON.stringify({ workspace: real })}\n`)
    await rename(tmp, WORKSPACE_STATE_FILE)
  }
  currentWorkspace = real
}

async function presetStamp(path) {
  const handle = await open(path, 'r')
  try {
    const metadata = await handle.stat()
    if (metadata.size > MAX_PRESET_BYTES) {
      throw new Error('DSH preset exceeds the bridge fingerprint size limit')
    }
    const digest = createHash('sha256')
    const chunk = Buffer.allocUnsafe(64 * 1024)
    let size = 0
    while (true) {
      const { bytesRead } = await handle.read(chunk, 0, chunk.length, null)
      if (bytesRead === 0) break
      size += bytesRead
      if (size > MAX_PRESET_BYTES) {
        throw new Error('DSH preset exceeds the bridge fingerprint size limit')
      }
      digest.update(chunk.subarray(0, bytesRead))
    }
    if (size !== metadata.size) {
      throw new Error('DSH preset changed while computing its bridge fingerprint')
    }
    return { mtimeMs: metadata.mtimeMs, size, digest: digest.digest('hex') }
  } finally {
    await handle.close()
  }
}

function samePresetStamp(left, right) {
  return left.mtimeMs === right.mtimeMs && left.size === right.size && left.digest === right.digest
}

function capabilitySessionId(cwd, presetId, presetPath, stamp) {
  const suffix = createHash('sha256')
    .update(JSON.stringify([cwd, presetId, presetPath, stamp.mtimeMs, stamp.size, stamp.digest]))
    .digest('hex')
    .slice(0, 24)
  return `${CAPABILITY_SESSION_PREFIX}-${suffix}`
}

function assertPersistedHelperHeader(header, cwd, presetId) {
  if (header.cwd !== cwd || header.agentPreset !== presetId) {
    throw new Error('persisted DSH bridge capability session metadata does not match the current workspace/preset')
  }
}

class ExternalChatGPTCapabilityAdapter {
  providerInfo(provider) {
    return { id: provider, name: 'ChatGPT Web (external capability identity)' }
  }

  listModels(provider) {
    return Promise.resolve([{
      provider,
      id: EXTERNAL_MODEL,
      name: 'ChatGPT Web',
      inputModalities: ['text', 'image'],
    }])
  }

  resolveModel(provider, model) {
    return Promise.resolve({
      provider,
      id: model,
      name: 'ChatGPT Web',
      inputModalities: ['text', 'image'],
    })
  }

  providerRetryPolicy() {
    return undefined
  }

  async *stream() {
    throw new Error('ChatGPT Web is external to DSH; the capability identity cannot perform model inference')
  }
}

function json(res, status, body) {
  let data = JSON.stringify(body)
  if (Buffer.byteLength(data) > MAX_RESPONSE_BYTES) {
    status = 500
    data = JSON.stringify({ error: 'bridge_error', message: 'DSH bridge response exceeds the configured size limit' })
  }
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(data),
    'cache-control': 'no-store',
  })
  res.end(data)
}

class BridgeRequestError extends Error {
  constructor(status, code, message) {
    super(message)
    this.name = 'BridgeRequestError'
    this.status = status
    this.code = code
  }
}

function replyBridgeFailure(ctx, res, operation, error) {
  if (error instanceof BridgeRequestError) {
    json(res, error.status, { error: error.code, message: error.message })
    return
  }
  ctx.logger.warn(`dsh-chatgpt-bridge: ${operation} failed`)
  ctx.logger.warn(error instanceof Error ? error : new Error(String(error)))
  json(res, 500, { error: 'bridge_error', message: 'internal DSH bridge operation failed' })
}

function toolCallLifetime(res) {
  const disconnected = new AbortController()
  const onClose = () => {
    if (!res.writableEnded && !disconnected.signal.aborted) {
      disconnected.abort(new Error('bridge client disconnected before tool call completed'))
    }
  }
  res.once('close', onClose)
  if (res.destroyed && !res.writableEnded) onClose()
  return {
    disconnected: disconnected.signal,
    signal: AbortSignal.any([
      AbortSignal.timeout(TOOL_CALL_TIMEOUT_MS),
      disconnected.signal,
    ]),
    dispose() {
      res.off('close', onClose)
    },
  }
}

function awaitWithinToolCallLifetime(promise, lifetime, onLateResolve) {
  const disposeLateValue = value => {
    void Promise.resolve(onLateResolve(value)).catch(() => {})
  }
  if (lifetime.signal.aborted) {
    promise.then(disposeLateValue, () => {})
    return Promise.reject(lifetime.signal.reason)
  }
  let removeAbortListener
  const aborted = new Promise((_, reject) => {
    const onAbort = () => reject(lifetime.signal.reason)
    lifetime.signal.addEventListener('abort', onAbort, { once: true })
    removeAbortListener = () => lifetime.signal.removeEventListener('abort', onAbort)
  })
  return Promise.race([promise, aborted]).then(
    value => {
      removeAbortListener?.()
      return value
    },
    error => {
      removeAbortListener?.()
      if (lifetime.signal.aborted) promise.then(disposeLateValue, () => {})
      throw error
    },
  )
}

async function materializeContentBlocks(ctx, blocks) {
  if (!Array.isArray(blocks)) return blocks
  const hasAttachmentImage = blocks.some(block => block?.type === 'image' && block.attachment)
  if (!hasAttachmentImage) return blocks
  const attachments = ctx.get('attachments')
  if (!attachments) throw new Error('DSH attachments service is unavailable for image materialization')

  const materialized = []
  for (const block of blocks) {
    if (block?.type !== 'image' || !block.attachment) {
      materialized.push(block)
      continue
    }
    const stored = await attachments.readImage(block.attachment)
    const mediaType = stored?.ref?.mediaType
    if (typeof mediaType !== 'string' || !mediaType) {
      throw new Error('DSH attachments service returned an image without a media type')
    }
    const data = Buffer.from(stored.data)
    if (data.byteLength > MAX_MATERIALIZED_IMAGE_BYTES) {
      throw new Error('DSH attachment image exceeds the bridge materialization size limit')
    }
    materialized.push({
      type: 'image',
      data: data.toString('base64'),
      mediaType,
    })
  }
  return materialized
}

async function materializeToolContent(ctx, result) {
  if (!result || typeof result !== 'object') return result
  const content = await materializeContentBlocks(ctx, result.content)
  let additionalContexts = result.additionalContexts
  if (Array.isArray(additionalContexts)) {
    const materializedContexts = []
    for (const message of additionalContexts) {
      if (!message || typeof message !== 'object') {
        materializedContexts.push(message)
        continue
      }
      materializedContexts.push({
        ...message,
        content: await materializeContentBlocks(ctx, message.content),
      })
    }
    additionalContexts = materializedContexts
  }
  return { ...result, content, additionalContexts }
}

async function readJson(req) {
  let size = 0
  const chunks = []
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    size += buffer.length
    if (size > MAX_BODY_BYTES) {
      throw new BridgeRequestError(413, 'request_too_large', 'request body too large')
    }
    chunks.push(buffer)
  }
  if (chunks.length === 0) return {}
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new BridgeRequestError(400, 'invalid_request', 'request body must be valid JSON')
    }
    throw error
  }
}

export function apply(ctx, config = {}) {
  const capabilityProfile = buildChatGPTCapabilityProfile(config)
  const allowedRootInput = config.allowedRoot == null ? '' : String(config.allowedRoot).trim()
  const resolvedRoot = allowedRootInput
    ? resolve(allowedRootInput.replace(/^~(?=$|[\\/])/, homedir()))
    : workspaceAllowedRoot
  let real
  try {
    real = realpathSync.native(resolvedRoot)
  } catch {
    throw new Error(allowedRootInput
      ? `dsh-chatgpt-bridge allowedRoot does not exist: ${resolvedRoot}`
      : `dsh-chatgpt-bridge workspace root ${resolvedRoot} does not exist — create it or set WORKSPACE_ALLOWED_ROOT`)
  }
  if (!statSync(real).isDirectory()) {
    throw new Error(`dsh-chatgpt-bridge allowedRoot is not a directory: ${real}`)
  }
  workspaceAllowedRoot = real
  const capabilityHandles = new Map()
  const instanceId = randomUUID()
  let toolRevision = 1
  let skillRevision = 1

  ctx.effect(() => ctx.on('tools/change', () => {
    toolRevision += 1
  }), 'dsh-chatgpt-bridge.tools-change')

  ctx.effect(() => ctx.on('skills/change', () => {
    skillRevision += 1
  }), 'dsh-chatgpt-bridge.skills-change')

  // Some native DSH tools gate their output modality through the calling
  // agent's exact model route. The bridge therefore registers a metadata-only
  // route describing ChatGPT Web's accepted modalities. Its stream method is a
  // hard failure: DSH can use the route only for capability checks, never for
  // inference. ChatGPT remains the sole reasoning/model agent.
  ctx.llm.registerAdapter([EXTERNAL_PROVIDER], new ExternalChatGPTCapabilityAdapter())

  function forceDisposeCapabilityEntry(helperSessionId, entry) {
    if (entry.disposePromise) return entry.disposePromise
    entry.disposePromise = (async () => {
      try {
        const handle = await entry.promise
        await handle.dispose()
      } catch {
        // Creation/resume failures already roll their unpublished scope back.
      } finally {
        if (capabilityHandles.get(helperSessionId) === entry) {
          capabilityHandles.delete(helperSessionId)
        }
      }
    })()
    return entry.disposePromise
  }

  function maybeDisposeCapabilityEntry(helperSessionId, entry) {
    if (!entry.stale || entry.leases !== 0) return undefined
    return forceDisposeCapabilityEntry(helperSessionId, entry)
  }

  function retireOtherCapabilityEntries(currentHelperSessionId) {
    for (const [helperSessionId, entry] of capabilityHandles) {
      if (helperSessionId === currentHelperSessionId) continue
      entry.stale = true
      void maybeDisposeCapabilityEntry(helperSessionId, entry)
    }
  }

  async function acquireCapabilityAgent(signal) {
    const assertActive = () => {
      if (signal?.aborted) throw signal.reason
    }
    const agents = ctx.get('agents')
    const presets = ctx.get('agentPresets')
    if (!agents || !presets) {
      throw new Error('DSH AgentRegistry and agentPresets services are required for bridge tool execution')
    }

    const presetId = presets.defaultId
    const preset = await presets.resolve(presetId)
    assertActive()
    const stamp = await presetStamp(preset.path)
    assertActive()
    if (presets.defaultId !== presetId) return acquireCapabilityAgent(signal)
    await ensureWorkspaceLoaded()
    const workspace = callWorkspace()
    const helperSessionId = capabilitySessionId(workspace, presetId, preset.path, stamp)

    let entry = capabilityHandles.get(helperSessionId)
    if (entry?.disposePromise) {
      await entry.disposePromise
      assertActive()
      entry = capabilityHandles.get(helperSessionId)
    }
    if (!entry) {
      const agentOptions = {
        provider: EXTERNAL_PROVIDER,
        model: EXTERNAL_MODEL,
      }
      const setup = async agentCtx => {
        const mountedPreset = await presets.mount(agentCtx, presetId)
        const mountedStamp = await presetStamp(mountedPreset.path)
        if (mountedPreset.path !== preset.path || !samePresetStamp(stamp, mountedStamp)) {
          throw new Error('DSH preset composition changed while creating the bridge capability agent')
        }
      }
      const promise = (async () => {
        const persistence = ctx.get('sessionPersistence')
        if (persistence) {
          const persisted = await persistence.list()
          assertActive()
          const persistedHelper = persisted.find(header => header.id === helperSessionId)
          if (persistedHelper) {
            assertPersistedHelperHeader(persistedHelper, workspace, presetId)
            return agents.resume({
              resumeSessionId: helperSessionId,
              agentOptions,
              setup,
            })
          }
        }
        assertActive()
        return agents.create({
          sessionId: helperSessionId,
          agentOptions,
          meta: {
            cwd: workspace,
            agentPreset: presetId,
          },
          // This Agent is only DSH's native execution identity. The bridge never
          // submits a prompt to it. Its metadata-only route cannot perform
          // inference; a workspace+preset+composition-generation id lets
          // persistence reuse the same helper across DSH restarts without
          // colliding with another workspace or pinning execution to stale scope.
          setup,
        })
      })()
      entry = {
        promise,
        leases: 0,
        stale: false,
        disposePromise: undefined,
      }
      capabilityHandles.set(helperSessionId, entry)
      promise.catch(() => {
        if (capabilityHandles.get(helperSessionId) === entry) {
          capabilityHandles.delete(helperSessionId)
        }
      })
    }

    entry.leases += 1
    let verified = false
    try {
      const handle = signal
        ? await awaitWithinToolCallLifetime(entry.promise, { signal }, () => {})
        : await entry.promise
      if (presets.defaultId !== presetId) {
        entry.stale = true
        entry.leases -= 1
        await maybeDisposeCapabilityEntry(helperSessionId, entry)
        return acquireCapabilityAgent(signal)
      }
      const currentPreset = await presets.resolve(presetId)
      const currentStamp = await presetStamp(currentPreset.path)
      if (
        presets.defaultId !== presetId
        || currentPreset.path !== preset.path
        || !samePresetStamp(stamp, currentStamp)
      ) {
        entry.stale = true
        entry.leases -= 1
        await maybeDisposeCapabilityEntry(helperSessionId, entry)
        return acquireCapabilityAgent(signal)
      }
      entry.stale = false
      retireOtherCapabilityEntries(helperSessionId)
      verified = true
      let released = false
      return {
        agent: handle.agent,
        async release() {
          if (released) return
          released = true
          entry.leases -= 1
          // A stale helper is no longer on the request's correctness path.
          // Its DSH dispose hook may itself stall, so do not keep a completed
          // bridge request alive waiting for best-effort retirement.
          void Promise.resolve(maybeDisposeCapabilityEntry(helperSessionId, entry)).catch(() => {})
        },
      }
    } catch (error) {
      if (!verified) entry.stale = true
      entry.leases -= 1
      if (signal?.aborted && entry.leases === 0 && capabilityHandles.get(helperSessionId) === entry) {
        capabilityHandles.delete(helperSessionId)
        void entry.promise.then(handle => handle.dispose(), () => {})
      } else {
        await maybeDisposeCapabilityEntry(helperSessionId, entry)
      }
      throw error
    }
  }

  ctx.effect(() => async () => {
    for (const [helperSessionId, entry] of capabilityHandles) {
      if (capabilityHandles.get(helperSessionId) === entry) {
        capabilityHandles.delete(helperSessionId)
      }
      // DSH creation/resume and handle disposal do not expose cancellation.
      // Teardown must therefore only trigger best-effort disposal, not wait on
      // an operation that can stall indefinitely. Pending handles are disposed
      // as soon as they arrive; settled handles follow the same path.
      void entry.promise.then(handle => handle.dispose(), () => {})
    }
  }, 'dsh-chatgpt-bridge.capability-agent')

  async function standingLookup() {
    const presets = ctx.get('agentPresets')
    if (!presets) {
      throw new Error('DSH agentPresets service is required for bridge discovery')
    }
    const presetId = presets.defaultId
    const scope = await presets.standingKeyFor(presetId)
    await ensureWorkspaceLoaded()
    return {
      scope,
      skillOptions: {
        cwd: callWorkspace(),
        scope,
      },
    }
  }

  function externalToolSchemas(scope) {
    return capabilityProfile.project(ctx.tools.schemas(scope))
  }

  async function assertExternalToolAvailable(toolName) {
    if (!capabilityProfile.allows(toolName)) {
      throw new BridgeRequestError(404, 'tool_unavailable', 'tool is unavailable in the external ChatGPT capability profile')
    }
    const { scope } = await standingLookup()
    if (!externalToolSchemas(scope).some(schema => schema.name === toolName)) {
      throw new BridgeRequestError(404, 'tool_unavailable', 'tool is unavailable in the current DSH preset')
    }
  }

  const jsonRender = (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]

  ctx.effect(() => ctx.tools.register({
    name: 'workspace_status',
    description: [
      'Report the DSH workspace root that subsequent tool calls execute under.',
      'Use workspace_switch to retarget it; allowed roots live under the operator-configured data tree.',
    ].join(' '),
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: { schema: {}, render: jsonRender },
    async execute() {
      await ensureWorkspaceLoaded()
      return {
        workspace: callWorkspace(),
        allowedRoot: workspaceAllowedRoot,
        binding: bindingSource(),
      }
    },
  }), 'chatgpt-bridge.workspace-status')

  ctx.effect(() => ctx.tools.register({
    name: 'workspace_switch',
    description: [
      'Switch the DSH workspace root for subsequent tool calls (bash cwd, filesystem roots, sandbox boundary).',
      'The switch binds the calling session only; other sessions keep their own workspace.',
      'path may use ~; it must resolve to an existing directory under the allowed data root.',
      'Use workspace_status to see the current root.',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Absolute or ~-relative path of the new workspace root.',
        },
      },
      required: ['path'],
      additionalProperties: false,
    },
    output: { schema: {}, render: jsonRender },
    async execute(rawArgs) {
      await ensureWorkspaceLoaded()
      const sessionId = sessionIdOfCall()
      const real = await resolveAllowedWorkspace(rawArgs?.path)
      const previous = activeWorkspaceFor(sessionId)
      if (real === previous) {
        // Explicit switch pins the session even when it lands on the fallback,
        // so a later global change doesn't move this conversation's root.
        if (sessionId && !sessionWorkspaces.has(sessionId)) bindSessionWorkspace(sessionId, real)
        return { workspace: real, changed: false }
      }
      if (sessionId) {
        bindSessionWorkspace(sessionId, real)
      } else {
        await setWorkspace(real)
      }
      // Retire live helpers so the next tool call rebuilds against the new root.
      for (const [id, entry] of capabilityHandles) {
        entry.stale = true
        void maybeDisposeCapabilityEntry(id, entry)
      }
      return { workspace: real, previousWorkspace: previous, changed: true }
    },
  }), 'chatgpt-bridge.workspace-switch')

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: `${PREFIX}/revision`,
    handler: async (_req, res) => {
      await ensureWorkspaceLoaded()
      let sandboxPolicy
      try {
        sandboxPolicy = ctx.get('sandboxPolicy')?.resolve?.({})
      } catch (error) {
        sandboxPolicy = `probe failed: ${String(error)}`
      }
      json(res, 200, { instanceId, toolRevision, skillRevision, capabilityProfile: capabilityProfile.id, workspace: activeWorkspace(), sandboxPolicy })
    },
  }))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: `${PREFIX}/tools`,
    handler: async (_req, res) => {
      try {
        const { scope } = await standingLookup()
        json(res, 200, {
          tools: externalToolSchemas(scope),
          scope: 'dsh-preset-standing',
          capabilityProfile: capabilityProfile.id,
        })
      } catch (error) {
        replyBridgeFailure(ctx, res, 'tool catalog', error)
      }
    },
  }))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: `${PREFIX}/skills`,
    handler: async (_req, res) => {
      try {
        const { skillOptions } = await standingLookup()
        const skills = await ctx.skills.list(skillOptions)
        json(res, 200, {
          skills: skills
            .filter(skill => skill.invocation?.modelInvocable === true)
            .map(skill => ({
              name: skill.name,
              description: skill.description,
              ...(skill.whenToUse ? { whenToUse: skill.whenToUse } : {}),
              source: skill.source,
              provider: skill.provider,
              ...(skill.resourceBase ? { resourceBase: skill.resourceBase } : {}),
            })),
        })
      } catch (error) {
        replyBridgeFailure(ctx, res, 'skill catalog', error)
      }
    },
  }))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: `${PREFIX}/skill`,
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        json(res, 405, { error: 'method_not_allowed' })
        return
      }
      try {
        const payload = await readJson(req)
        const skillName = typeof payload?.name === 'string' ? payload.name.trim() : ''
        if (!skillName) {
          json(res, 400, { error: 'invalid_request', message: 'name must be a non-empty string' })
          return
        }
        const { skillOptions } = await standingLookup()
        const summary = (await ctx.skills.list(skillOptions)).find(skill => skill.name === skillName)
        if (!summary || summary.invocation?.modelInvocable !== true) {
          json(res, 404, { error: 'skill_unavailable', message: `skill "${skillName}" is unavailable for model invocation` })
          return
        }
        const skill = await ctx.skills.get(skillName, skillOptions)
        if (!skill || skill.invocation?.modelInvocable !== true) {
          json(res, 404, { error: 'skill_unavailable', message: `skill "${skillName}" is unavailable for model invocation` })
          return
        }
        json(res, 200, {
          skill: {
            name: skill.name,
            description: skill.description,
            ...(skill.whenToUse ? { whenToUse: skill.whenToUse } : {}),
            source: skill.source,
            provider: skill.provider,
            ...(skill.resourceBase ? { resourceBase: skill.resourceBase } : {}),
            content: skill.content,
          },
        })
      } catch (error) {
        replyBridgeFailure(ctx, res, 'skill load', error)
      }
    },
  }))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: `${PREFIX}/call`,
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        json(res, 405, { error: 'method_not_allowed' })
        return
      }
      let lifetime
      let capability
      try {
        const payload = await readJson(req)
        const toolName = typeof payload?.name === 'string' ? payload.name.trim() : ''
        if (!toolName) {
          json(res, 400, { error: 'invalid_request', message: 'name must be a non-empty string' })
          return
        }
        lifetime = toolCallLifetime(res)
        const toolArguments = payload.arguments ?? {}
        if (typeof toolArguments !== 'object' || toolArguments === null || Array.isArray(toolArguments)) {
          throw new BridgeRequestError(400, 'invalid_request', 'arguments must be an object when supplied')
        }
        const sessionId = typeof payload.sessionId === 'string' && payload.sessionId.trim()
          ? payload.sessionId.trim().slice(0, 256)
          : null
        await assertExternalToolAvailable(toolName)
        capability = await awaitWithinToolCallLifetime(
          callContext.run({ sessionId }, () => acquireCapabilityAgent(lifetime.signal)),
          lifetime,
          lateCapability => lateCapability.release(),
        )
        if (lifetime.disconnected.aborted) return
        const result = await awaitWithinToolCallLifetime(
          callContext.run({ sessionId }, () => ctx.tools.execute({
            callId: `chatgpt-${randomUUID()}`,
            name: toolName,
            arguments: toolArguments,
            signal: lifetime.signal,
            agent: capability.agent,
          })),
          lifetime,
          () => {},
        )
        if (lifetime.disconnected.aborted) return
        const materialized = await awaitWithinToolCallLifetime(
          materializeToolContent(ctx, result),
          lifetime,
          () => {},
        )
        if (!lifetime.disconnected.aborted) json(res, 200, materialized)
      } catch (error) {
        if (!lifetime?.disconnected.aborted) {
          replyBridgeFailure(ctx, res, 'tool call', error)
        }
      } finally {
        await capability?.release()
        lifetime?.dispose()
      }
    },
  }))
}

#!/usr/bin/env node
// Validate DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS before the DSH host starts.
// The value is a JSON array of existing absolute physical directory paths.
// This mirrors DSH's stricter grant enforcement so bad config fails at launch.
const path = require('node:path')
const fs = require('node:fs')

const raw = process.env.DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS || ''
if (!raw.trim()) process.exit(0)

let roots
try {
  roots = JSON.parse(raw)
} catch {
  fail('must be a JSON array of absolute directory paths')
}
if (!Array.isArray(roots)) fail('must be a JSON array')
for (const root of roots) {
  if (typeof root !== 'string' || !root.trim()) fail('every entry must be a non-empty string')
  if (!path.isAbsolute(root)) fail(`entry is not absolute: ${root}`)
  if (!fs.existsSync(root)) fail(`entry does not exist: ${root}`)
  if (!fs.statSync(root).isDirectory()) fail(`entry is not a directory: ${root}`)
  const physical = path.resolve(fs.realpathSync.native(root))
  if (path.resolve(root) !== physical) fail(`entry must use its physical path spelling: ${root}`)
}
process.exit(0)

function fail(detail) {
  console.error(`DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS ${detail}`)
  process.exit(1)
}

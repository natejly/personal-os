// Picks how a macOS build is signed. Loaded by electron-builder.config.cjs (and so by
// scripts/adhoc-sign.cjs's run), in order of preference:
//
//   1. 'env'   CSC_NAME or CSC_LINK already set (Developer ID / CI): electron-builder uses it as is.
//   2. 'local' A "Grain Local Signing" code-signing identity is in this Mac's keychain (see
//              docs/releasing.md): sign with it. The designated requirement is then tied to that
//              certificate instead of each build's cdhash, so macOS privacy grants (Full Disk
//              Access, Automation, ...) survive rebuilds.
//   3. 'adhoc' Neither: electron-builder skips signing and scripts/adhoc-sign.cjs ad-hoc signs.
//
// GRAIN_ADHOC_SIGN=1 forces 'adhoc' even when the local identity exists.
const { execFileSync } = require('child_process')

const LOCAL_IDENTITY = 'Grain Local Signing'

function hasLocalIdentity() {
  if (process.platform !== 'darwin' || process.env.GRAIN_ADHOC_SIGN === '1') return false
  try {
    const out = execFileSync('security', ['find-identity', '-v', '-p', 'codesigning'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore']
    })
    return out.includes(`"${LOCAL_IDENTITY}"`)
  } catch {
    return false
  }
}

let cached = null

/** Returns 'env' | 'local' | 'adhoc'. For 'local' it also sets CSC_NAME for electron-builder. */
function resolveSigning() {
  if (cached) return cached
  if (process.env.CSC_NAME || process.env.CSC_LINK) {
    cached = process.env.GRAIN_LOCAL_SIGNING === '1' ? 'local' : 'env'
  } else if (hasLocalIdentity()) {
    process.env.CSC_NAME = LOCAL_IDENTITY
    process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false'
    process.env.GRAIN_LOCAL_SIGNING = '1'
    cached = 'local'
  } else {
    cached = 'adhoc'
  }
  return cached
}

module.exports = { LOCAL_IDENTITY, resolveSigning }

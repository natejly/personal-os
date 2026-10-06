// electron-builder afterSign hook. When no signing identity is configured, electron-builder skips
// signing, which leaves the (modified) Electron app with an invalid signature and macOS on Apple
// Silicon refuses to launch it. Ad-hoc sign the nested Python binaries and the app so local builds run.
// When a real identity signed the app (CSC_NAME / CSC_LINK, or the local "Grain Local Signing"
// identity picked by scripts/mac-signing.cjs), do nothing: re-signing ad-hoc would throw that away.
const { execFileSync } = require('child_process')
const { join } = require('path')
const { resolveSigning } = require('./mac-signing.cjs')

exports.default = async function adhocSign(context) {
  if (context.electronPlatformName !== 'darwin' || resolveSigning() !== 'adhoc') return
  const app = join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  const ent = join(context.packager.projectDir, 'build', 'entitlements.mac.plist')
  const sign = (p, extra = []) =>
    execFileSync('codesign', ['--force', '--sign', '-', '--options', 'runtime', '--entitlements', ent, ...extra, p], {
      stdio: 'inherit'
    })
  const backend = join(app, 'Contents', 'Resources', 'backend', 'python')
  const files = execFileSync(
    'find',
    [backend, '-type', 'f', '(', '-name', '*.so', '-o', '-name', '*.dylib', '-o', '-path', '*/bin/python3.12', ')'],
    { maxBuffer: 64 * 1024 * 1024 }
  )
    .toString()
    .split('\n')
    .filter(Boolean)
  for (const f of files) sign(f)
  sign(app, ['--deep'])
}

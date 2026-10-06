// electron-builder config. Kept as JS (not package.json "build") so signing and notarization can
// follow the environment: see docs/releasing.md.
//
// Signing (scripts/mac-signing.cjs): CSC_NAME / CSC_LINK if set; else the self-signed "Grain Local
// Signing" identity when it is in this Mac's keychain, so privacy grants survive rebuilds; else
// electron-builder skips signing and scripts/adhoc-sign.cjs (afterSign) ad-hoc signs, so a local build
// still launches. Notarization runs only when all three APPLE_* variables are present.
const { resolveSigning, LOCAL_IDENTITY } = require('./scripts/mac-signing.cjs')

const signingMode = resolveSigning()
const signing = signingMode !== 'adhoc'
if (signingMode === 'local') console.log(`  • signing with local identity "${LOCAL_IDENTITY}"`)
const notarize = Boolean(
  process.env.APPLE_ID && process.env.APPLE_TEAM_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD
)

module.exports = {
  appId: 'com.natejly.grain',
  productName: 'Grain',
  files: ['out/**/*', 'package.json'],
  // The python-build-standalone + backend bundle made by scripts/bundle-backend.sh.
  extraResources: [{ from: 'build/backend-bundle', to: 'backend' }],
  afterSign: 'scripts/adhoc-sign.cjs',
  publish: [{ provider: 'github', owner: 'natejly', repo: 'personal-os' }],
  mac: {
    category: 'public.app-category.productivity',
    target: ['dmg', 'zip'], // electron-updater applies updates from the zip
    hardenedRuntime: true,
    gatekeeperAssess: false,
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    // null skips electron-builder's signing (and its keychain auto-discovery); the hook ad-hoc signs instead.
    ...(signing ? {} : { identity: null }),
    // A self-signed identity gets no secure timestamp, and asking Apple's server for one costs seconds
    // per file across thousands of bundled Python files.
    ...(signingMode === 'local' ? { timestamp: 'none' } : {}),
    notarize,
    extendInfo: {
      NSMicrophoneUsageDescription:
        'Grain records audio for meeting notes and voice input. Audio is transcribed with the provider you configure.',
      NSSpeechRecognitionUsageDescription:
        'Grain transcribes meeting and activity audio on this Mac so the recording never has to leave the machine.',
      NSAudioCaptureUsageDescription:
        'Grain captures system audio for meeting notes and the activity monitor when you enable that source.',
      NSAppleEventsUsageDescription:
        'Grain uses AppleScript and Shortcuts to read the frontmost app and to run actions you ask for.',
      NSScreenCaptureUsageDescription:
        'Grain can read on-screen context for the Activity monitor, only when you turn it on.',
      NSCameraUsageDescription: 'Grain uses the camera only for photos and video you choose to capture.',
      NSContactsUsageDescription: 'Grain reads and updates Contacts when you ask it to.',
      NSCalendarsUsageDescription: 'Grain reads and updates Calendar events when you ask it to.',
      NSCalendarsFullAccessUsageDescription: 'Grain reads and updates Calendar events when you ask it to.',
      NSRemindersUsageDescription: 'Grain reads and updates Reminders when you ask it to.',
      NSRemindersFullAccessUsageDescription: 'Grain reads and updates Reminders when you ask it to.',
      NSDesktopFolderUsageDescription: 'Grain reads and writes files on your Desktop when you ask it to.',
      NSDocumentsFolderUsageDescription: 'Grain reads and writes files in Documents when you ask it to.',
      NSDownloadsFolderUsageDescription: 'Grain reads and writes files in Downloads when you ask it to.'
    }
  }
}

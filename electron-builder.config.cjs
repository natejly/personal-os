// electron-builder config. Kept as JS (not package.json "build") so signing and notarization can
// follow the environment: see docs/releasing.md.
//
// Signing: with CSC_NAME / CSC_LINK set, electron-builder signs with that identity. Otherwise it skips
// signing and scripts/adhoc-sign.cjs (afterSign) ad-hoc signs, so a local build still launches.
// Notarization runs only when all three APPLE_* variables are present.
const signing = Boolean(process.env.CSC_NAME || process.env.CSC_LINK)
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
    notarize,
    extendInfo: {
      NSMicrophoneUsageDescription:
        'Grain records audio for voice input. Audio is transcribed with the provider you configure.',
      NSSpeechRecognitionUsageDescription:
        'Grain can transcribe voice input on this Mac so the audio never has to leave the machine.',
      NSAppleEventsUsageDescription:
        'Grain uses AppleScript and Shortcuts to read the frontmost app and to run actions you ask for.',
      NSScreenCaptureUsageDescription:
        'Grain captures the screen when you teach it a task or ask it to look at your screen.',
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

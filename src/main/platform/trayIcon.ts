/**
 * The Grain rice-grain silhouette as an alpha-only PNG (regenerate with scripts/make-icons.py).
 * electron-builder ships only `out/**` and there is no `resources/`, so the icon is inline.
 */
import { nativeImage } from 'electron'

const ICON_1X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAxklEQVR4nL2TMQrCQBREX2IEQbG1Fls7PYDWeg0LL2DllQQbwXtYewMJ2KqJ8mEWlrjRrIUDyyT798/Mh134ExKtaKRavlBUs8MA6BKBlngMHIEcOAOjgPgbXHEOXIEnUIo3qmVUP7xmOzgE9kAfeGjPUnVoGH2npru4EM8q54LRbc6bmkolMD4B7U/zZ+K15154KRYh9zQg1BMXqpvwFjjo3/aDSMUTb+YLsAo51yERT4GlLlBd0q8iDo2cqzBHa/zpAUXhBfxFJIMms99SAAAAAElFTkSuQmCC'
const ICON_2X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABgElEQVR4nO2Wvy4FQRTGf7O7CgoiQqJQu71Oo1Qp9CpKDyDegdcgkXgKPAFuoVQQCkIlwa5M8k1ysrn37sbO0OxJJid3Z+d833znz13orbd/NhcxTlgVUEaK22geMB/z3KVWIDM39SQGwDzwAgxNfK9IdMsNiT3gBvg28p8BM9p3qcAHwIVAKwMeiBzovSIF+Iak9kCfAi31+0vrGpiKqUAmvwa8GfBqhAqVCC7oTGcSmYIsAo/mptUEAq/AUiwCuYKcT7h5WKEGhkY1FyPvmy3Aw75X4aR2vrP8Vwo8Tvqwwv5OjC7I5ddr8k6S35N8AObayJ81EAiHt+SbZnypM0fqlKLrJHTylw2VH/Y8gTtgNsYUdPLTwH1DCmxt+HRFKT4n73P5VOvxet5DZ+zHArd/qYV6+nuEArYlD1PM/kL+WCAfArW18A7spgC3KiwDtyOK7hRY7SK7a/lOpcreBlaAZ3XG0ID79CQzN+Z51mKW/Cpwm++/8i8/PnvrjVT2A/a5o6tDiOFLAAAAAElFTkSuQmCC'

/** The silhouette as a template image: macOS tints it for light, dark and highlighted menu bars. */
export const templateTrayIcon = (): Electron.NativeImage => {
  const img = nativeImage.createFromDataURL(ICON_1X)
  img.addRepresentation({ scaleFactor: 2, dataURL: ICON_2X })
  img.setTemplateImage(true)
  return img
}

/** The same silhouette filled with one colour, for trays that do not tint (the alpha channel is kept). */
export const coloredTrayIcon = (rgb: readonly [number, number, number] = [0x8f, 0xb0, 0x8a]): Electron.NativeImage => {
  const src = nativeImage.createFromDataURL(ICON_2X)
  const { width, height } = src.getSize()
  const px = Buffer.from(src.toBitmap())
  // toBitmap is BGRA.
  for (let i = 0; i < px.length; i += 4) {
    px[i] = rgb[2]
    px[i + 1] = rgb[1]
    px[i + 2] = rgb[0]
  }
  return nativeImage.createFromBitmap(px, { width, height })
}

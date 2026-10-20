import type { Settings, ShortcutState } from '@shared/types'
import { useStore } from '../../store'
import { DEFAULT_ZOOM, stepZoom } from '../../lib/zoom'
import { homeModuleOn } from '../../moduleToggles'
import { ACCENTS, CODE_FONTS, CUSTOM_ACCENT, MASCOTS, PRESETS, SURFACE_TONES, TEXT_TONES, UI_FONTS, accentId, activePreset, codeFontId, hexColor, isLightTheme, mascotId, surfaceToneId, textToneId, uiFontId } from '../../lib/appearance'
import { tip } from '../../lib/tip'
import TypographyControls from '../../features/notes/TypographyMenu'
import VoiceInputSettings from '../VoiceInputSettings'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'
import { SHORTCUT_KEYS, type ShortcutKey } from './useShortcuts'

export const THEMES: { id: Settings['theme']; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' }
]

export default function GeneralTab({ draft, patch, shortcuts }: TabProps & { shortcuts: Partial<Record<ShortcutKey, ShortcutState>> }): JSX.Element {
  const zoom = draft.uiZoom ?? DEFAULT_ZOOM
  return (
    <section>
      <Sec id="look">
      <h4>Appearance</h4>
      <div className="setting-row">
        <span className="toggle-text"><b>Theme</b></span>
        <div className="seg" role="radiogroup" aria-label="Theme">
          {THEMES.map((t) => (
            <button key={t.id} type="button" role="radio" aria-checked={draft.theme === t.id} className={draft.theme === t.id ? 'on' : ''} onClick={() => patch({ theme: t.id })}>{t.label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row stack">
        <span className="toggle-text"><b>Accent colour</b><small>{accentId(draft.accent) === CUSTOM_ACCENT ? hexColor(draft.accentCustom) : ACCENTS.find((a) => a.id === accentId(draft.accent))?.label}</small></span>
        <div className="accent-picks" role="radiogroup" aria-label="Accent color">
          {ACCENTS.map((a) => {
            const on = accentId(draft.accent) === a.id
            return (
              <button
                key={a.id}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={a.label}
                title={a.label}
                className={`accent-swatch${on ? ' on' : ''}`}
                style={{ background: a.swatch }}
                onClick={() => patch({ accent: a.id })}
              />
            )
          })}
          <label className={`accent-custom${accentId(draft.accent) === CUSTOM_ACCENT ? ' on' : ''}`} title="Custom colour"
            style={{ background: accentId(draft.accent) === CUSTOM_ACCENT ? hexColor(draft.accentCustom) : undefined }}>
            {accentId(draft.accent) === CUSTOM_ACCENT ? null : '+'}
            <input type="color" aria-label="Custom accent colour" value={hexColor(draft.accentCustom)}
              onChange={(e) => patch({ accent: CUSTOM_ACCENT, accentCustom: e.target.value })} />
          </label>
        </div>
      </div>
      <details className="adv-group more-appearance" data-opens="look">
        <summary>More appearance</summary>
      <div className="preset-row" role="radiogroup" aria-label="Appearance presets">
        {PRESETS.map((p) => {
          const on = activePreset(draft) === p.id
          return (
            <button key={p.id} type="button" role="radio" aria-checked={on} className={`preset-card${on ? ' on' : ''}`}
              onClick={() => patch({ theme: p.theme, accent: p.accent, uiFont: p.uiFont, codeFont: p.codeFont })}>
              <span className="preset-prev" style={{ background: p.bg, color: p.fg, fontFamily: UI_FONTS.find((f) => f.id === p.uiFont)?.stack }}>
                Aa<i style={{ background: p.dot }} />
              </span>
              <b>{p.label}</b>
            </button>
          )
        })}
      </div>
      <div className="setting-row">
        <span className="toggle-text"><b>Text colour</b><small>{TEXT_TONES.find((t) => t.id === textToneId(draft.textTone))?.label}</small></span>
        <div className="accent-picks" role="radiogroup" aria-label="Text color">
          {TEXT_TONES.map((t) => {
            const on = textToneId(draft.textTone) === t.id
            return (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={t.label}
                title={t.label}
                className={`accent-swatch${on ? ' on' : ''}`}
                style={{ background: isLightTheme(draft.theme) ? t.light : t.dark }}
                onClick={() => patch({ textTone: t.id })}
              />
            )
          })}
        </div>
      </div>
      <div className="setting-row">
        <span className="toggle-text"><b>Background</b></span>
        <div className="seg" role="radiogroup" aria-label="Background">
          {SURFACE_TONES.map((t) => (
            <button key={t.id} type="button" role="radio" aria-checked={surfaceToneId(draft.surfaceTone) === t.id} className={surfaceToneId(draft.surfaceTone) === t.id ? 'on' : ''} onClick={() => patch({ surfaceTone: t.id })}>{t.label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row">
        <span className="toggle-text"><b>Mascot</b></span>
        <div className="seg" role="radiogroup" aria-label="Mascot">
          {MASCOTS.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={mascotId(draft.mascot) === m.id} className={mascotId(draft.mascot) === m.id ? 'on' : ''} onClick={() => patch({ mascot: m.id })}>{m.label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row stack">
        <span className="toggle-text"><b>Interface font</b></span>
        <div className="font-picks" role="radiogroup" aria-label="Interface font">
          {UI_FONTS.map((f) => (
            <button key={f.id} type="button" role="radio" aria-checked={uiFontId(draft.uiFont) === f.id} className={uiFontId(draft.uiFont) === f.id ? 'on' : ''} style={{ fontFamily: f.stack }} onClick={() => patch({ uiFont: f.id })}>{f.label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row stack">
        <span className="toggle-text"><b>Code font</b></span>
        <div className="font-picks" role="radiogroup" aria-label="Code font">
          {CODE_FONTS.map((f) => (
            <button key={f.id} type="button" role="radio" aria-checked={codeFontId(draft.codeFont) === f.id} className={codeFontId(draft.codeFont) === f.id ? 'on' : ''} style={{ fontFamily: f.stack }} onClick={() => patch({ codeFont: f.id })}>{f.label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row">
        <span className="toggle-text"><b>Zoom</b></span>
        <div className="seg" role="group" aria-label="Zoom">
          <button type="button" aria-label="Zoom out" title={tip('Zoom out', 'zoom-out')} onClick={() => patch({ uiZoom: stepZoom(zoom, -1) })}>−</button>
          <button type="button" disabled>{zoom}%</button>
          <button type="button" aria-label="Zoom in" title={tip('Zoom in', 'zoom-in')} onClick={() => patch({ uiZoom: stepZoom(zoom, 1) })}>+</button>
          <button type="button" title={tip('Actual size', 'zoom-reset')} onClick={() => patch({ uiZoom: DEFAULT_ZOOM })}>Reset</button>
        </div>
      </div>
      </details>
      <Switch title="Daily recap on new chats" checked={homeModuleOn(draft, 'recap')} onChange={(on) => patch({ homeWidgets: { ...(draft.homeWidgets ?? {}), recap: on } })} />
      <Switch title="Start chat windows as blobs" checked={!!draft.compactChats} onChange={(compactChats) => patch({ compactChats })} />
      <div className="setting-row">
        <span className="toggle-text"><b>Default file font</b></span>
        <TypographyControls value={draft.docTypography ?? {}} onChange={(t) => patch({ docTypography: { ...(draft.docTypography ?? {}), ...t } })}
          onReset={draft.docTypography && Object.keys(draft.docTypography).length ? () => patch({ docTypography: {} }) : undefined} />
      </div></Sec>
      <Sec id="notifications">
        <h4>Notifications</h4>
        <Switch title="Notify me when a chat finishes" checked={draft.chatNotify !== false} onChange={(chatNotify) => patch({ chatNotify })} />
        <Switch title="Notify me about scheduled jobs" checked={draft.notifyJobs !== false} onChange={(notifyJobs) => patch({ notifyJobs })} />
      </Sec>
      <Sec id="shortcuts">
        <h4>Shortcuts <button type="button" className="link-btn" title={tip('Keyboard shortcuts', 'help')} onClick={() => useStore.getState().openHelp('shortcuts')}>Show all shortcuts</button></h4>
        {SHORTCUT_KEYS.map((k) => { const s = shortcuts[k]; return s && !s.ok && <p key={k} className="test-msg fail">{s.message ?? `${s.accelerator} could not be registered.`}</p> })}
        <label><span className="toggle-text"><b>Dictation key</b></span>
          <input value={draft.dictationChord ?? ''} onChange={(e) => patch({ dictationChord: e.target.value })} placeholder="Control+Alt+D" spellCheck={false} />
        </label>
        <VoiceInputSettings />
        <label><span className="toggle-text"><b>Bring widgets to front</b></span>
          <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })} placeholder={shortcuts.gatherShortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
        </label>
        <label><span className="toggle-text"><b>Quick note shortcut</b></span>
          <input value={draft.quickCaptureShortcut ?? ''} onChange={(e) => patch({ quickCaptureShortcut: e.target.value })} placeholder="CommandOrControl+Shift+Space" spellCheck={false} />
        </label>
        <label><span className="toggle-text"><b>Quick ask shortcut</b></span>
          <input value={draft.quickAskShortcut ?? ''} onChange={(e) => patch({ quickAskShortcut: e.target.value })} placeholder="Alt+Space" spellCheck={false} />
        </label>
      </Sec>
    </section>
  )
}

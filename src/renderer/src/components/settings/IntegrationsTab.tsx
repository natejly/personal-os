import GoogleSettings from '../GoogleSettings'
import PlannerMailSettings from '../PlannerMailSettings'
import Switch from './Switch'
import TelegramSettings from '../TelegramSettings'
import { Sec, type TabProps } from './shared'
import { useStore } from '../../store'

export default function IntegrationsTab({ draft, patch, onSaveCreds }: TabProps & { onSaveCreds: () => Promise<void> }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const hold = draft.gmailSendHold ?? { enabled: true, seconds: 90 }
  return (
    <section>
      <Sec id="accounts">
      <h4>Mail and Calendar</h4>
      <Switch title="Hold outgoing email so I can undo" checked={hold.enabled} onChange={(enabled) => patch({ gmailSendHold: { ...hold, enabled } })} />
      <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} secretSaved={!!settings.googleClientSecretSet} backupLabel={draft.driveBackupLabel ?? ''} onChange={(p) => patch(p)}
        onSaveCreds={onSaveCreds} />
      </Sec>
      <Sec id="connectors"><h4>Connectors</h4>
      <div className="setting-row">
        <span className="toggle-text"><b>Connectors</b></span>
        <button className="ghost-btn" type="button" onClick={() => { useStore.getState().setLibraryTab('connectors'); useStore.getState().setView('library'); useStore.getState().setSettingsOpen(false) }}>Open Library</button>
      </div>
      <PlannerMailSettings /></Sec>
      <Sec id="web-search">
        <h4>Web search</h4>
        <label><span className="toggle-text"><b>Firecrawl key</b></span><input type="password" value={draft.firecrawlApiKey ?? ''} onChange={(e) => patch({ firecrawlApiKey: e.target.value })} placeholder={settings.firecrawlApiKeySet ? 'Saved. Type to replace' : 'fc-…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Brave Search key</b></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Tavily key</b></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Exa key</b></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Your own search server</b></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
        <label><span className="toggle-text"><b>GitHub token</b></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
        <Switch title="Retry blocked pages through a reader service" checked={draft.readerFallback !== false} onChange={(readerFallback) => patch({ readerFallback })} />
      </Sec>
      <TelegramSettings draft={draft} patch={patch} />
    </section>
  )
}

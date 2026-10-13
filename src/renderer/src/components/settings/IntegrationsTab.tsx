import { useStore } from '../../store'
import GoogleSettings from '../GoogleSettings'
import PlannerMailSettings from '../PlannerMailSettings'
import TelegramSettings from '../TelegramSettings'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'

/** `onSaveCreds` writes the Google client id and secret at once: the sign-in that follows opens the browser and needs them stored. */
export default function IntegrationsTab({ draft, patch, onSaveCreds }: TabProps & { onSaveCreds: () => Promise<void> }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const hold = draft.gmailSendHold ?? { enabled: true, seconds: 90 }
  return (
    <section>
      <h3>Integrations</h3>
      <Sec id="accounts">
      <Switch title="Hold outgoing email so I can undo" checked={hold.enabled} onChange={(enabled) => patch({ gmailSendHold: { ...hold, enabled } })} />
      <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} secretSaved={!!settings.googleClientSecretSet} backupLabel={draft.driveBackupLabel ?? ''} onChange={(p) => patch(p)}
        onSaveCreds={onSaveCreds} />
      </Sec>
      <Sec id="connectors"><h4>Connectors</h4>
      <div className="setting-row">
        <span className="toggle-text"><b>Connectors</b><small>Tools from other services, added and managed in the Library.</small></span>
        <button className="ghost-btn" type="button" onClick={() => { useStore.getState().setLibraryTab('connectors'); useStore.getState().setView('library'); useStore.getState().setSettingsOpen(false) }}>Open Library</button>
      </div>
      <PlannerMailSettings /></Sec>
      <Sec id="web-search">
        <h4>Web search</h4>
        <p className="muted small">All optional. A Firecrawl key makes it the first engine for web search and page reads. Without one, web search uses Exa, then DuckDuckGo.</p>
        <label><span className="toggle-text"><b>Firecrawl key</b><small>Searches and reads pages first when set. Firecrawl sees the page address. {settings.firecrawlEnvKey && !settings.firecrawlApiKeySet ? 'Using FIRECRAWL_API_KEY from the environment.' : 'Empty uses FIRECRAWL_API_KEY from the environment, if set.'}</small></span><input type="password" value={draft.firecrawlApiKey ?? ''} onChange={(e) => patch({ firecrawlApiKey: e.target.value })} placeholder={settings.firecrawlApiKeySet ? 'Saved. Type to replace' : 'fc-…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Brave Search key</b></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Tavily key</b><small>An alternative to Brave.</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Exa key</b><small>Optional; lifts the rate limit.</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
        <label><span className="toggle-text"><b>Your own search server</b><small>A SearXNG address, searched beside Exa. Needs <code>json</code> under search.formats.</small></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
        <label><span className="toggle-text"><b>GitHub token</b><small>Optional. Empty uses your <code>gh</code> login.</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
        <Switch title="Retry blocked pages through a reader service" help="For pages that are blocked or need JavaScript. The reader service sees the page address." checked={draft.readerFallback !== false} onChange={(readerFallback) => patch({ readerFallback })} />
      </Sec>
      <Sec id="texting">
        <h4>Telegram bot</h4>
        <TelegramSettings draft={draft} patch={patch} />
      </Sec>
    </section>
  )
}

// Importing this file registers every dedicated tool card (each card file calls registerToolCard on load).
// One import line per card; keep the list alphabetical-ish and do not reorder others' lines.
import './TaskCard'
import './FileCard'
// --- workstreams add their card import below this line ---
import './CalendarCard'
import './EmailCard'
import './ShellCard'
import './PythonCard'
import './BrowserCard'
import './BrowserApprovalCard'
import './ViewImageCard'
import './DocumentCard'
import './DeskCards'
import './SandboxCard'
import './ShipChecklistCard'
import './CodingSessionCard'
import './ShareCards'

export { TOOL_CARDS } from './registry'

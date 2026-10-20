/** The built-in extensions, activated in order when the shell's registry is first read. Nothing is loaded at runtime. */
import { activateExtension, extensionModules } from './host'
import * as todos from './todos/extension'
import * as health from './health/extension'
import * as calendar from './calendar/extension'
import * as mail from './mail/extension'
import * as spaces from './spaces/extension'
import * as coding from './coding/extension'
import * as memoryTree from './memory-tree/extension'
import * as claudeCode from './claude-code/extension'
import * as latex from './latex/extension'

activateExtension('todos', todos)
activateExtension('health', health)
activateExtension('calendar', calendar)
activateExtension('mail', mail)
activateExtension('spaces', spaces)
activateExtension('coding', coding)
activateExtension('memory-tree', memoryTree)
activateExtension('claude-code', claudeCode)
activateExtension('latex', latex)

export { extensionModules }

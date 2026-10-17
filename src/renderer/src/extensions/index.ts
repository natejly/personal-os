/** The built-in extensions, activated in order when the shell's registry is first read. Nothing is loaded at runtime. */
import { activateExtension, extensionModules } from './host'
import * as todos from './todos/extension'
import * as health from './health/extension'

activateExtension('todos', todos)
activateExtension('health', health)

export { extensionModules }

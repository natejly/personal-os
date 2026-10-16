/** The built-in modules and the views the built-in extensions registered, in that order. Nothing is loaded at runtime. */
import type { ModuleDef } from './types'
import { todosModule } from '../features/todos/module'
import { extensionModules } from '../extensions'

export const MODULES: ModuleDef[] = [todosModule, ...extensionModules]

export const moduleForView = (view: string): ModuleDef | undefined => MODULES.find((m) => m.view?.id === view)

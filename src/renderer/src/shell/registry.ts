/** The built-in modules, in registration order. Nothing is loaded at runtime. */
import type { ModuleDef } from './types'
import { todosModule } from '../features/todos/module'
import { healthModule } from '../features/health/module'

export const MODULES: ModuleDef[] = [todosModule, healthModule]

export const moduleForView = (view: string): ModuleDef | undefined => MODULES.find((m) => m.view?.id === view)
export const moduleHome = (key: string): ModuleDef | undefined => MODULES.find((m) => m.home?.key === key)

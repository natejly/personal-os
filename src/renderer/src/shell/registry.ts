/** The views the built-in extensions registered, in activation order. Nothing is loaded at runtime. */
import type { ModuleDef } from './types'
import { extensionModules } from '../extensions'

export const MODULES: ModuleDef[] = extensionModules

export const moduleForView = (view: string): ModuleDef | undefined => MODULES.find((m) => m.view?.id === view)

import { useStore } from '../../store'
import ModelDuels from '../ModelDuels'
import ModelsSettings from '../ModelsSettings'
import { Sec, type TabProps } from './shared'

export default function ModelsTab({ draft, patch }: TabProps): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  return (
    <section>
      <h3>Models</h3>
      <Sec id="keys"><ModelsSettings settings={settings} models={models} pickerModels={draft.pickerModels} setPickerModels={(pickerModels) => patch({ pickerModels })} /></Sec>
      {draft.devTools === true && <Sec id="duels"><ModelDuels /></Sec>}
    </section>
  )
}

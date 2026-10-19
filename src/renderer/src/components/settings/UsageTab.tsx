import UsageView from '../UsageView'
import { Sec } from './shared'

export default function UsageTab(): JSX.Element {
  return (
    <section className="usage-tab">
      <Sec id="usage"><UsageView /></Sec>
    </section>
  )
}

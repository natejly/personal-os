import UsageView from '../UsageView'
import { Sec } from './shared'

export default function UsageTab(): JSX.Element {
  return (
    <section className="usage-tab">
      <h3>Usage</h3>
      <Sec id="usage"><UsageView /></Sec>
    </section>
  )
}

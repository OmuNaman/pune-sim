import { useEffect } from 'react'
import { useSelection } from '../stores/selection'
import { PersonDossier } from '../features/PersonDossier'
import { PlaceCard } from '../features/PlaceCard'
import { Panel } from '../components/Panel'

export function Inspector({ runId, t, order }: {
  runId: string
  t: number
  /** person ids by ordinal — how a map click becomes an id */
  order: string[] | undefined
}) {
  const sel = useSelection((s) => s.sel)
  const select = useSelection((s) => s.select)

  // The map picks an ordinal, because the agent buffer has no ids in it. The
  // roster turns that back into a person the moment it is loaded.
  useEffect(() => {
    if (sel.kind === 'person-ord' && order && order[sel.ord]) {
      select({ kind: 'person', id: order[sel.ord] })
    }
  }, [sel, order, select])

  if (sel.kind === 'none') return null

  return (
    <div className="absolute top-[76px] right-3 bottom-[130px] w-[370px] z-20 flex flex-col">
      {sel.kind === 'place' && <PlaceCard runId={runId} placeId={sel.id} t={t} />}
      {sel.kind === 'person' && <PersonDossier runId={runId} personId={sel.id} t={t} />}
      {sel.kind === 'person-ord' && (
        <Panel className="p-3 text-[12px]" title="person">finding them…</Panel>
      )}
    </div>
  )
}

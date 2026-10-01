/**
 * Data Explorer (spec §4.2): a synced market's fields, narrowed in More Filters down to whole
 * categories, whole subcategories or single datasets. Syncing lives in BRAIN › Sync.
 */

import { useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { fmt } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useDatasetPick } from '@/screens/data/dataset-pick'
import { Button, Metric, Page, PageHeader } from '@/ui/kit'
import { FieldsTab } from './fields'
import { MarketBar } from './market'
import { labFilter, useFieldFilter } from './state'

export function DataScreen() {
  const [scope, update] = useScope('data')
  const picking = useDatasetPick((s) => s.active)

  // A pick follows the market shown here.
  useEffect(() => {
    if (picking) useDatasetPick.getState().follow(scope)
  }, [picking, scope])

  return (
    <Page>
      <PageHeader
        title="Data Explorer"
        description="Browse, filter and compare the Data Fields you synced from BRAIN, locally."
      />
      {picking && <PickBar />}
      <MarketBar scope={scope} update={update} />
      <FieldsTab scope={scope} />
    </Page>
  )
}

/** While a lab picks datasets: how many are ticked, where to tick them, and the way back to it. */
function PickBar() {
  const navigate = useNavigate()
  const count = useDatasetPick((s) => s.ids.length)
  const back = (done: boolean) => {
    const pick = useDatasetPick.getState()
    const to = pick.from
    // The filter goes with the datasets: the lab searches only the fields it shows.
    if (done) pick.finish(labFilter(useFieldFilter.getState().filter))
    else pick.cancel()
    void navigate({ to })
  }

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 rounded-lg border border-hairline bg-surface-1 p-3">
      <Metric boxed size="sm" label="Datasets Selected" value={fmt.int(count)} />
      <span className="min-w-0 flex-1 text-body-compact text-pretty text-ink-subtle">
        Tick whole categories, whole subcategories or single datasets in More Filters. The lab uses
        only the fields your filters show.
      </span>
      <Button variant="ghost" onClick={() => back(false)}>
        Cancel
      </Button>
      <Button variant="primary" disabled={count === 0} onClick={() => back(true)}>
        Done
      </Button>
    </div>
  )
}

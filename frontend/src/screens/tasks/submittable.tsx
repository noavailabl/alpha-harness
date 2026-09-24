/**
 * Every submittable Alpha from every task, on the same pane a single task's results use.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useRefetchOn } from '@/lib/ws'
import { AlphaPane } from '@/screens/tasks/alpha-pane'
import { labTasks, type RankedAlpha, type TaskAlpha } from '@/screens/tasks/api'
import { compareAlphas, METRIC_COLUMNS, SETTING_COLUMNS } from '@/screens/tasks/columns'
import type { Column, Sort } from '@/ui/table'

/** The task's own columns, minus Checks Failed — every row here has none — plus its ID and task. */
const columns = (): Column<RankedAlpha>[] => [
  {
    key: 'alphaId',
    header: 'Alpha ID',
    width: 'minmax(100px,0.9fr)',
    sortable: true,
    cell: (r) => <span className="num text-ink-muted">{r.alphaId}</span>,
  },
  ...SETTING_COLUMNS,
  ...METRIC_COLUMNS,
  {
    key: 'taskName',
    header: 'Task',
    width: 'minmax(180px,1.4fr)',
    sortable: true,
    cell: (r) => <span className="truncate text-ink-muted">{(r as TaskAlpha).taskName ?? ''}</span>,
  },
]

export function SubmittableAlphas() {
  const query = useQuery({ queryKey: ['submittable-alphas'], queryFn: labTasks.submittable })
  // Its own key, refreshed at most every 30s: reading every task's Alphas takes about a second,
  // too long to redo on each of the Tasks screen's two-second updates.
  useRefetchOn('studies', ['submittable-alphas'], 30_000)
  const [sort, setSort] = useState<Sort>({ key: 'sharpe', desc: true })
  const rows = useMemo<RankedAlpha[]>(() => query.data ?? [], [query.data])

  return (
    <AlphaPane
      title="Submittable Alphas"
      rows={rows}
      columns={columns}
      compare={compareAlphas}
      poolColumnAfter="investability"
      sort={sort}
      onSort={setSort}
      loading={query.isPending}
      error={query.error}
      onRefresh={() => query.refetch()}
    />
  )
}

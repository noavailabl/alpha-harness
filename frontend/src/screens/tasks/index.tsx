/** Tasks: everything the labs added. Only here does a task run, wait for cores, pause or stop. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import {
  CopyIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  SquareIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { cn } from '@/lib/cn'
import { DASH, fmt } from '@/lib/format'
import { useNow } from '@/lib/now'
import { useRefetchOn } from '@/lib/ws'
import { DatasetChips, useDatasetTree } from '@/screens/data/dataset-chips'
import { DetailSheet } from '@/screens/pool/detail'
import { MAX_SIMULATIONS } from '@/screens/research-labs/lab-task'
import { type LabTask, labTasks, type RankedAlpha } from '@/screens/tasks/api'
import {
  AFTER_COST_HEADER,
  DELAY,
  INVESTABILITY,
  QuickBadge,
  SharpeCell,
  TRUNCATION,
  taskStatus,
} from '@/screens/tasks/columns'
import { resultsMarkdown } from '@/screens/tasks/copy'
import { SubmittableAlphas } from '@/screens/tasks/submittable'
import {
  Badge,
  Button,
  Disclosure,
  Empty,
  ErrorNotice,
  Field,
  Fieldset,
  Input,
  LINK,
  Metric,
  Notice,
  Page,
  PageHeader,
  Panel,
  Progress,
  Segmented,
  signTone,
  TEXT_TONE,
} from '@/ui/kit'
import { Confirm, Dialog, Menu } from '@/ui/overlay'
import { type Column, DataTable } from '@/ui/table'

/** Matches `labs.params.SETTINGS_SAMPLER`. */
const SETTINGS_SAMPLER = 'settings-sampler'

const TOP_COLUMNS: Column<RankedAlpha>[] = [
  {
    key: 'expression',
    header: 'Expression',
    width: 'minmax(280px,3fr)',
    cell: (r) => (
      <span className="flex min-w-0 items-center gap-1.5">
        <QuickBadge alpha={r} />
        <span className="num block truncate text-ink" title={r.expression ?? undefined}>
          {r.expression ?? DASH}
        </span>
      </span>
    ),
  },
  {
    key: 'sharpe',
    header: 'Sharpe',
    width: '90px',
    align: 'right',
    cell: (r) => <SharpeCell value={r.sharpe} />,
  },
  {
    // The held-out years, where an Alpha that only fits its train years shows its decay.
    key: 'testSharpe',
    header: 'Test Sharpe',
    width: '100px',
    align: 'right',
    cell: (r) => (
      <span className={TEXT_TONE[signTone(r.testSharpe)]}>{fmt.ratio(r.testSharpe)}</span>
    ),
  },
  {
    key: 'fitness',
    header: 'Fitness',
    width: '80px',
    align: 'right',
    cell: (r) => fmt.ratio(r.fitness),
  },
  {
    key: 'turnover',
    header: 'Turnover',
    width: '88px',
    align: 'right',
    cell: (r) => fmt.pct(r.turnover),
  },
  {
    key: 'universe',
    header: 'Universe',
    width: '96px',
    cell: (r) => r.settings?.universe ?? DASH,
  },
  {
    key: 'neutralization',
    header: 'Neutralization',
    width: '120px',
    cell: (r) => r.settings?.neutralization ?? DASH,
  },
]

/**
 * The Alpha's Sharpe once 5 bps is charged against each day's own turnover, normalized to ten
 * years of data. It sits beside the gross Sharpe rather than replacing it.
 *
 * Empty until the daily PnL and turnover are downloaded, which is a request per Alpha.
 */
const AFTER_COST_SHARPE: Column<RankedAlpha> = {
  key: 'afterCostSharpe',
  header: AFTER_COST_HEADER,
  width: '132px',
  align: 'right',
  cell: (r) =>
    r.afterCostSharpe == null ? (
      <span className="text-ink-subtle" title="Daily PnL not downloaded yet">
        {DASH}
      </span>
    ) : (
      <span className={cn('num', TEXT_TONE[signTone(r.afterCostSharpe)])}>
        {fmt.ratio(r.afterCostSharpe)}
      </span>
    ),
}

const setting = (key: string, header: string, width: string): Column<RankedAlpha> => ({
  key,
  header,
  width,
  cell: (r) => <span className="num">{(r.settings?.[key] as string | undefined) ?? DASH}</span>,
})

/**
 * A Settings Sampler row is only ever the same expression, so the settings lead instead and
 * Sharpe closes.
 */
const SAMPLER_COLUMNS: Column<RankedAlpha>[] = [
  {
    key: 'number',
    header: 'Trial',
    width: '120px',
    cell: (r) => (
      <span className="num flex items-center gap-1.5 text-ink-subtle">
        {r.number}
        <QuickBadge alpha={r} />
      </span>
    ),
  },
  // Region, Delay, Universe, Neutralization, Max Trade, Max Position — the order a market is
  // named in everywhere, so a reader's eye lands in the same place on every screen.
  setting('region', 'Region', '96px'),
  DELAY,
  setting('universe', 'Universe', '116px'),
  // Takes the slack, so the table fills its pane and Sharpe closes at the right edge.
  setting('neutralization', 'Neutralization', 'minmax(180px,1fr)'),
  INVESTABILITY,
  {
    key: 'sharpe',
    header: 'Sharpe',
    width: '100px',
    align: 'right',
    cell: (r) => <SharpeCell value={r.sharpe} />,
  },
  AFTER_COST_SHARPE,
]

/** What a task searches for leads the table when it is not Sharpe, which the table shows anyway. */
const topColumns = (task: LabTask): Column<RankedAlpha>[] =>
  task.lab === SETTINGS_SAMPLER
    ? task.truncationAgent
      ? [...SAMPLER_COLUMNS.slice(0, 5), TRUNCATION, ...SAMPLER_COLUMNS.slice(5)]
      : SAMPLER_COLUMNS
    : task.objectiveLabel === 'Sharpe'
      ? TOP_COLUMNS
      : [
          ...TOP_COLUMNS.slice(0, 1),
          {
            key: 'value',
            header: task.objectiveLabel,
            width: '112px',
            align: 'right',
            cell: (r) => <span className={TEXT_TONE[signTone(r.value)]}>{fmt.ratio(r.value)}</span>,
          },
          ...TOP_COLUMNS.slice(1),
        ]

type Act = { action: 'runAll' } | { action: 'run' | 'pause' | 'stop' | 'remove'; task: LabTask }

export function TasksScreen() {
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: ['lab-tasks'], queryFn: labTasks.list })
  useRefetchOn('studies', ['lab-tasks'], 2_000)
  useRefetchOn('simulations', ['lab-tasks'], 5_000)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editing, setEditing] = useState<LabTask | null>(null)
  const [renaming, setRenaming] = useState<LabTask | null>(null)
  const [confirming, setConfirming] = useState<Act | null>(null)
  const [alphaId, setAlphaId] = useState<string | null>(null)
  const [view, setView] = useState<'tasks' | 'submittable'>('tasks')

  // Nothing picked yet: open on what is running, then stay there. Re-deriving this every render
  // would move the pane out from under the reader the moment that task finished.
  const running = list.data?.tasks.find((t) => t.status === 'RUNNING')?.id ?? null
  useEffect(() => {
    if (running != null) setSelectedId((id) => id ?? running)
  }, [running])

  const act = useMutation({
    meta: { inline: true },
    mutationFn: async (a: Act) => {
      if (a.action === 'runAll') await labTasks.runAll()
      else await labTasks[a.action](a.task.id)
    },
    onSuccess: () => {
      setConfirming(null)
      for (const key of [['lab-tasks'], ['bar'], ['today'], ['simulations']])
        void queryClient.invalidateQueries({ queryKey: key })
    },
  })

  // A failed action leaves its notice behind; the next dialog must not open wearing it.
  const ask = (a: Act) => {
    act.reset()
    setConfirming(a)
  }

  const all = list.data?.tasks ?? []
  const slots = list.data?.slots ?? 8
  const open = all.filter((t) => t.status !== 'COMPLETE' && t.status !== 'FAILED')
  const total = (tasks: LabTask[], pick: (t: LabTask) => number) =>
    tasks.reduce((n, t) => n + pick(t), 0)
  const runningCores = total(
    open.filter((t) => t.status === 'RUNNING'),
    (t) => t.cores,
  )
  const assignedCores = total(open, (t) => t.cores)
  const waiting = all.filter((t) => t.status === 'QUEUED').length
  const fresh = all.filter((t) => t.status === 'IDLE').length
  const selected = all.find((t) => t.id === selectedId) ?? null
  const copy = confirming ? confirmCopy(confirming, fresh) : null

  const columns: Column<LabTask>[] = [
    {
      key: 'task',
      header: 'Task',
      width: 'minmax(220px,2fr)',
      cell: (t) => (
        <span className="block min-w-0 truncate" title={t.datasetIds.join(', ')}>
          <span className="text-ink">
            {t.name ?? (
              <>
                {t.labName}
                {t.templateName ? ` · ${t.templateName}` : ''}
              </>
            )}
          </span>
          <span className="text-ink-subtle">
            {t.name && ` · ${t.labName}${t.templateName ? ` · ${t.templateName}` : ''}`}
            {/* A sweep spans many markets, so naming the source Alpha's one would mislead. */}
            {t.lab === SETTINGS_SAMPLER ? (
              <>
                {/* A sweep started from a typed expression has no source Alpha: "" not null. */}
                {t.alphaId && (
                  <>
                    {' · '}
                    <span className="num">{t.alphaId}</span>
                  </>
                )}
                {' · '}
                <span className="num">{fmt.int(t.markets)}</span>
                {t.markets === 1 ? ' Market' : ' Markets'}
              </>
            ) : (
              <>
                {' · '}
                <span className="num">{`${t.region} D${t.delay}`}</span>
                {/* Neither seeds nor datasets: say nothing rather than report "0 datasets"
                    about something the task never had. */}
                {(t.seeds > 0 || t.datasetIds.length > 0) && (
                  <>
                    {' · '}
                    <span className="num">
                      {fmt.int(t.seeds > 0 ? t.seeds : t.datasetIds.length)}
                    </span>
                    {t.seeds > 0 ? ' seeds' : t.datasetIds.length === 1 ? ' dataset' : ' datasets'}
                  </>
                )}
              </>
            )}
          </span>
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      // Wide enough for the longest badge ("Not Started" measures 96px) plus the cell's px-3.
      width: '124px',
      cell: (t) => <TaskBadge task={t} />,
    },
    {
      key: 'cores',
      header: 'Cores',
      width: '64px',
      align: 'right',
      cell: (t) => fmt.int(t.cores),
    },
    {
      key: 'simulations',
      header: 'Simulations',
      width: 'minmax(220px,1.5fr)',
      cell: (t) => (
        <span className="flex w-full min-w-0 items-center gap-2">
          <Progress
            className="flex-1"
            value={t.target > 0 ? t.simulated / t.target : 0}
            label="Simulated"
          />
          <span className="num shrink-0 text-body-compact">
            {fmt.int(t.simulated)} / {fmt.int(t.target)}
          </span>
        </span>
      ),
    },
    {
      key: 'best',
      header: 'Best',
      width: '96px',
      align: 'right',
      cell: (t) => (
        <span title={t.objectiveLabel} className={TEXT_TONE[signTone(t.best)]}>
          {fmt.ratio(t.best)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      width: '186px',
      align: 'right',
      cell: (t) => (
        <Actions
          task={t}
          onAct={(action) =>
            action === 'pause' ? act.mutate({ action, task: t }) : ask({ action, task: t })
          }
          onEdit={() => setEditing(t)}
          onRename={() => setRenaming(t)}
        />
      ),
    },
  ]

  return (
    <Page>
      <PageHeader
        title="Tasks"
        actions={
          <Button
            variant="primary"
            disabled={fresh === 0}
            onClick={() => ask({ action: 'runAll' })}
          >
            <PlayIcon />
            Run All
          </Button>
        }
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          boxed
          label="Running Cores"
          value={list.isPending ? DASH : `${fmt.int(runningCores)} / ${fmt.int(slots)}`}
        />
        <Metric
          boxed
          label="Cores Assigned"
          value={list.isPending ? DASH : fmt.int(assignedCores)}
          tone={assignedCores > slots ? 'warn' : 'neutral'}
          hint={waiting > 0 ? `${fmt.int(waiting)} waiting` : undefined}
        />
        <Metric
          boxed
          label="Simulations Assigned"
          value={list.isPending ? DASH : fmt.int(total(open, (t) => t.target))}
        />
        <Metric
          boxed
          label="Simulated"
          value={list.isPending ? DASH : fmt.int(total(open, (t) => t.simulated))}
        />
      </div>
      {list.isError && all.length > 0 && (
        <ErrorNotice error={list.error} title="Could not load tasks" />
      )}
      {act.isError && confirming === null && <ErrorNotice error={act.error} />}

      <Panel
        actions={
          <Segmented
            label="View"
            items={[
              { value: 'tasks', label: 'Tasks' },
              { value: 'submittable', label: 'Submittable Alphas' },
            ]}
            value={view}
            onChange={setView}
          />
        }
      >
        {view === 'submittable' ? (
          <SubmittableAlphas onOpenAlpha={setAlphaId} />
        ) : list.data && all.length === 0 ? (
          <Empty title="No tasks yet">
            <Link to="/labs" className={LINK}>
              Open Research Labs
            </Link>
          </Empty>
        ) : (
          <DataTable
            label="Tasks"
            rows={all}
            columns={columns}
            rowKey={(t) => String(t.id)}
            onRowClick={(t) => setSelectedId(t.id)}
            // Held through hover, which otherwise repaints the row as if nothing were picked.
            rowClass={(t) =>
              t.id === selectedId ? 'bg-primary-subtle hover:bg-primary-subtle' : undefined
            }
            loading={list.isPending}
            error={list.error}
          />
        )}
      </Panel>
      {view === 'tasks' && selected && <TaskDetail task={selected} onOpenAlpha={setAlphaId} />}

      {editing && (
        <EditTask key={editing.id} task={editing} slots={slots} onClose={() => setEditing(null)} />
      )}
      {renaming && (
        <RenameTask key={renaming.id} task={renaming} onClose={() => setRenaming(null)} />
      )}
      <DetailSheet alphaId={alphaId} onClose={() => setAlphaId(null)} />
      <Confirm
        open={confirming !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setConfirming(null)
            act.reset()
          }
        }}
        title={copy?.title ?? ''}
        confirmLabel={copy?.label ?? 'Confirm'}
        danger={confirming?.action === 'stop' || confirming?.action === 'remove'}
        pending={act.isPending}
        onConfirm={() => confirming && act.mutate(confirming)}
      >
        {copy?.body}
        {act.isError && <ErrorNotice error={act.error} className="mt-3" />}
      </Confirm>
    </Page>
  )
}

function confirmCopy(a: Act, fresh: number): { title: string; label: string; body?: string } {
  switch (a.action) {
    case 'runAll':
      return {
        title: `Run ${fmt.int(fresh)} ${fresh === 1 ? 'task' : 'tasks'}?`,
        label: 'Run All',
      }
    case 'run':
      if (a.task.status === 'PAUSED') return { title: 'Resume this task?', label: 'Resume' }
      if (a.task.status === 'FAILED')
        return {
          title: 'Retry this task?',
          label: 'Retry',
          body: 'Simulations it sent before it failed are scored on the way.',
        }
      return { title: 'Run this task?', label: 'Run Task' }
    case 'stop':
      // The second press, on a task that has been stopping and has not stopped. It says
      // what it will cost, because forcing gives up on simulations the quota already paid
      // for — which is the right trade only once the ordinary stop has failed.
      if (a.task.stopping)
        return {
          title: 'Force this task to stop?',
          label: 'Force Stop',
          body: 'It is waiting on simulations that have not come back. Forcing cancels what it can on BRAIN, ends the task and frees its cores. Any simulation that finishes anyway is still kept in Alphas.',
        }
      return {
        title: 'Stop this task?',
        label: 'Stop Task',
        body: 'Simulations already sent finish and are kept; the rest come off the queue.',
      }
    default:
      return {
        title: 'Remove this task?',
        label: 'Remove Task',
        body: 'The Alphas it found stay in Alphas.',
      }
  }
}

function TaskBadge({ task }: { task: LabTask }) {
  const { label, tone } = taskStatus(task)
  // A task that paused or failed by itself says why: on the list, too, where a bare "Paused"
  // read as the app stopping for no reason.
  const why = (task.status === 'PAUSED' || task.status === 'FAILED') && task.message
  return (
    <Badge tone={tone} {...(why ? { title: why } : {})}>
      {why && <TriangleAlertIcon className="size-3" aria-hidden />}
      {label}
    </Badge>
  )
}

/** A failed task can run again: simulations it sent before it failed are scored then. */
const RUN_LABEL = { IDLE: 'Run', PAUSED: 'Resume', FAILED: 'Retry' } as const

function Actions({
  task,
  onAct,
  onEdit,
  onRename,
}: {
  task: LabTask
  onAct: (action: 'run' | 'pause' | 'stop' | 'remove') => void
  onEdit: () => void
  onRename: () => void
}) {
  const { status, stopping } = task
  const finished = status === 'COMPLETE' || status === 'FAILED'
  return (
    // Inside a clickable row: a click on these must not also select the row.
    <span
      role="group"
      aria-label="Task actions"
      className="flex items-center justify-end gap-0.5"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {(status === 'IDLE' || status === 'PAUSED' || status === 'FAILED') && (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={RUN_LABEL[status]}
          title={RUN_LABEL[status]}
          onClick={() => onAct('run')}
        >
          <PlayIcon />
        </Button>
      )}
      {(status === 'RUNNING' || status === 'QUEUED') && !stopping && (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Pause"
          title="Pause"
          onClick={() => onAct('pause')}
        >
          <PauseIcon />
        </Button>
      )}
      {/* Stays through `stopping`, unlike Pause and Edit. A task waiting on a simulation
          that never comes back is exactly when someone needs this button, and hiding it
          left them with a task holding cores and nothing on screen to press. */}
      {(status === 'RUNNING' || status === 'PAUSED' || status === 'QUEUED') && (
        <Button
          size="icon-sm"
          variant={stopping ? 'danger' : 'ghost'}
          aria-label={stopping ? 'Force stop' : 'Stop'}
          title={stopping ? 'Force stop' : 'Stop'}
          onClick={() => onAct('stop')}
        >
          <SquareIcon />
        </Button>
      )}
      {!finished && !stopping && (
        <Button size="icon-sm" variant="ghost" aria-label="Edit" title="Edit" onClick={onEdit}>
          <PencilIcon />
        </Button>
      )}
      {status !== 'RUNNING' && (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Remove"
          title="Remove"
          onClick={() => onAct('remove')}
        >
          <Trash2Icon />
        </Button>
      )}
      <TaskActionsMenu task={task} onRename={onRename} />
    </span>
  )
}

/** Task actions that are not one-click enough to earn a button of their own. */
function TaskActionsMenu({ task, onRename }: { task: LabTask; onRename: () => void }) {
  const navigate = useNavigate()
  return (
    <Menu
      trigger={
        <Button size="icon-sm" variant="ghost" aria-label={`More actions for task ${task.id}`}>
          <EllipsisIcon />
        </Button>
      }
      items={[
        { label: 'Rename', onClick: onRename },
        {
          label: 'Submission Planner',
          disabled: !task.simulated,
          onClick: () =>
            void navigate({ to: '/tools/submission-planner', search: { task: task.id } }),
        },
      ]}
    />
  )
}

function TaskDetail({
  task,
  onOpenAlpha,
}: {
  task: LabTask
  onOpenAlpha: (alphaId: string) => void
}) {
  const top = useQuery({
    // Its own key, refreshed at most every 10s: the whole sweep is a megabyte or more on a
    // big task, too much to redo on each of the task list's two-second updates.
    queryKey: ['lab-task-top', task.id],
    // The whole sweep is worth scrolling; the table virtualises, so the rows are cheap.
    queryFn: () => labTasks.top(task.id, Math.min(Math.max(task.target, 50), 5000)),
  })
  useRefetchOn('studies', ['lab-task-top', task.id], 10_000)
  // Arrives sorted on the objective already.
  const found = top.data ?? []
  // Red only where a check refuses the Alpha. A row still waiting on BRAIN is green like a
  // passing one: nothing has said no, which is the question this pane answers. Whether it is
  // submittable *yet* is the Submittable count's job, and that one does hold pending back.
  // A Quick Alpha nothing refused is neither: its Full run decides, and replaces it once back.
  const awaitingFull = (r: RankedAlpha) => r.quick && r.refusedBy.length === 0
  const verdict = (r: RankedAlpha) =>
    r.submittable || r.pending
      ? 'bg-pnl-positive-tint'
      : awaitingFull(r)
        ? ''
        : 'bg-pnl-negative-tint'
  const rowClass = verdict

  const done = task.status === 'COMPLETE' || task.status === 'FAILED'
  // Three outcomes and nothing else. Submittable: nothing has refused it, pending checks
  // included, which is what makes the figure an estimate. Error: a check BRAIN could not run,
  // or a simulation that returned no Alpha at all. Unsubmittable: every other refusal.
  const pending = found.filter((r) => r.pending).length
  const green = found.filter((r) => r.submittable || r.pending).length
  const erroredRows = found.filter((r) => r.errored && !(r.submittable || r.pending)).length
  const errors = erroredRows + task.failed
  const red = found.length - green - erroredRows - found.filter(awaitingFull).length

  const title = [
    task.name,
    task.labName,
    task.templateName,
    task.lab === SETTINGS_SAMPLER ? task.alphaId : `${task.region} D${task.delay}`,
  ]
    .filter(Boolean)
    .join(' · ')
  const description =
    task.lab === SETTINGS_SAMPLER
      ? // Held at the source Alpha's values for every simulation in the sweep.
        `${fmt.int(task.markets)} Markets · Decay ${task.decay ?? DASH} · ${task.truncationAgent ? 'Truncation Agent' : `Truncation ${task.truncation ?? DASH}`} · NaN Handling ${task.nanHandling ?? DASH}`
      : task.lab === 'super-alpha'
        ? `${task.universe ?? DASH} · SuperAlphas from your submitted Alphas`
        : task.seeds > 0
          ? `${task.universe ?? DASH} · ${fmt.int(task.seeds)} seeds · Population ${fmt.int(task.population)} · Mutation ${fmt.pct(task.mutationRate, 0)}`
          : `Decay ${task.decay ?? DASH} · ${fmt.int(task.fields)} fields`
  const copyResults = () =>
    navigator.clipboard.writeText(resultsMarkdown(task, found)).then(
      () => toast.success(`Copied ${fmt.int(found.length)} results`),
      (e: unknown) => toast.error(errorMessage(e)),
    )

  return (
    <Panel
      title={title}
      description={description}
      actions={
        <>
          <Button size="sm" variant="ghost" disabled={!found.length} onClick={copyResults}>
            <CopyIcon />
            Copy Results
          </Button>
          <TaskBadge task={task} />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <TaskDatasets task={task} />
        {/* As many boxes as there are figures, sharing the row: some only show when they
            have something to say, and a fixed grid left a hole where they were. */}
        <div className="flex flex-wrap gap-3 *:min-w-44 *:flex-1">
          <Metric
            boxed
            label="Simulated"
            value={`${fmt.int(task.simulated)} / ${fmt.int(task.target)}`}
            hint={[
              task.cached > 0 && `${fmt.int(task.cached)} from cache, no quota spent`,
              task.fullRuns > 0 &&
                `plus ${fmt.int(task.fullRuns)} Quick ${task.fullRuns === 1 ? 'Alpha' : 'Alphas'} run again in full`,
            ]
              .filter(Boolean)
              .join(' · ')}
          />
          {/* Nothing is in flight once a task is over, so the box would only ever read 0. */}
          {!done && <Metric boxed label="In Flight" value={fmt.int(task.queued + task.running)} />}
          {/* `~` because the pending rows counted here have checks BRAIN has not run
              yet, any one of which can still come back FAIL. */}
          <Metric
            boxed
            tone="profit"
            label="Submittable"
            value={
              <>
                {pending > 0 && '~'}
                {fmt.int(green)}
              </>
            }
          />
          <Metric
            boxed
            tone={red > 0 ? 'loss' : 'neutral'}
            label="Unsubmittable"
            value={fmt.int(red)}
          />
          {errors > 0 && (
            <Metric
              boxed
              tone="loss"
              label="Error"
              value={fmt.int(errors)}
              hint={task.failed > 0 ? `${fmt.int(task.failed)} returned no Alpha` : ''}
            />
          )}
          <Elapsed task={task} done={done} />
        </div>
        {task.message && (
          <Notice tone={task.status === 'FAILED' ? 'error' : 'info'} title={task.message} />
        )}
        {task.failures.length > 0 && (
          <Notice
            tone="error"
            title={`${fmt.int(task.failed)} simulation${task.failed === 1 ? '' : 's'} returned no Alpha. BRAIN said:`}
          >
            <ul className="flex flex-col gap-1">
              {task.failures.map((f) => (
                <li key={f.reason}>
                  <span className="num">{fmt.int(f.count)}×</span> {f.reason}
                </li>
              ))}
            </ul>
          </Notice>
        )}
        {task.template && (
          <Disclosure summary="Template">
            <code className="num text-body-compact break-all text-ink">{task.template}</code>
          </Disclosure>
        )}
        {top.isError && top.data && (
          <ErrorNotice error={top.error} title="Could not load the best Alphas" />
        )}
        {/* A sweep's results are a comparison across markets, which needs more room than a
            card: the whole set, grouped by region and correlated, gets its own page. */}
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="secondary"
            render={<Link to="/tasks/$taskId" params={{ taskId: String(task.id) }} />}
          >
            <ExternalLinkIcon />
            Open Full Results
          </Button>
        </div>
        <DataTable
          label={task.lab === SETTINGS_SAMPLER ? 'Results' : 'Top Alphas'}
          rows={found}
          columns={topColumns(task)}
          rowKey={(r) => String(r.trialId)}
          onRowClick={(r) => r.alphaId && onOpenAlpha(r.alphaId)}
          rowClass={rowClass}
          loading={top.isPending}
          error={top.error}
          empty={
            task.failed > 0
              ? 'No Alphas back yet: every simulation so far returned none, for the reasons above.'
              : 'No Alphas back yet.'
          }
        />
        {task.lab === SETTINGS_SAMPLER && (
          // A two-column grid rather than padded text: the equals signs line up whatever the
          // labels are and whatever the font does.
          <p className="num grid w-fit grid-cols-[auto_auto] gap-x-2 gap-y-0.5 text-body-compact text-ink-subtle">
            <span className="text-pnl-positive-text">GREEN</span>
            <span>= PASS or WARNING or PENDING</span>
            <span className="text-pnl-negative-text">RED</span>
            <span>= FAIL or ERROR</span>
          </p>
        )}
      </div>
    </Panel>
  )
}

/** Its own component so the clock re-renders one box a second, not the task and its table. */
/** The datasets a task searches, placed in its market's catalog. */
function TaskDatasets({ task }: { task: LabTask }) {
  const { region, delay, universe, datasetIds } = task
  const scope =
    region && delay !== null && universe && datasetIds.length > 0
      ? { instrumentType: 'EQUITY', region, delay, universe }
      : null
  const { tree, nameOf, ready } = useDatasetTree(scope)
  if (datasetIds.length === 0) return null
  return <DatasetChips tree={tree} value={datasetIds} nameOf={nameOf} ready={ready} />
}

function Elapsed({ task, done }: { task: LabTask; done: boolean }) {
  // Ticking while there is something to tick: a finished task's elapsed time is fixed, and a
  // timer behind it would wake the page every second to redraw the same string.
  const now = useNow(done ? 0 : 1000)
  // A task that has been told to run but has no cores yet is waiting, not running, and
  // saying "0s elapsed" for twenty minutes of that is not an account of anything. From when
  // it first ran otherwise — older rows predate that being recorded and fall back to created.
  const waiting = task.status === 'QUEUED'
  const from = waiting ? (task.queuedAt ?? task.createdAt) : (task.startedAt ?? task.createdAt)
  const began = Date.parse(from ?? '')
  const ended = task.finishedAt ? Date.parse(task.finishedAt) : now
  const elapsed = Number.isNaN(began) ? null : Math.max(0, (ended - began) / 1000)
  return (
    <Metric
      boxed
      label={waiting ? 'Waiting' : 'Time Elapsed'}
      value={elapsed == null ? DASH : fmt.duration(elapsed)}
      hint={done ? '' : waiting ? 'for cores to free up' : 'still running'}
    />
  )
}

function RenameTask({ task, onClose }: { task: LabTask; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(task.name ?? '')
  const rename = useMutation({
    meta: { inline: true },
    mutationFn: () => labTasks.rename(task.id, name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lab-tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['submittable-alphas'] })
      onClose()
    },
  })

  return (
    <Dialog
      open
      onOpenChange={(isOpen) => !isOpen && onClose()}
      title="Rename Task"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="rename-task" loading={rename.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="rename-task"
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          rename.mutate()
        }}
      >
        <Field label="Name" hint="Leave it blank to go back to the lab's own name.">
          <Input
            autoFocus
            maxLength={128}
            placeholder={[task.labName, task.templateName].filter(Boolean).join(' · ')}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        {rename.isError && <ErrorNotice error={rename.error} title="Could not rename the task" />}
      </form>
    </Dialog>
  )
}

function EditTask({ task, slots, onClose }: { task: LabTask; slots: number; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [cores, setCores] = useState(task.cores)
  const [simulations, setSimulations] = useState(String(task.target))
  const count = Number(simulations)
  // Below what it has already simulated the task is finished the moment it is saved, and the
  // count reads past its own target. Stop is the way to end a task early.
  const least = Math.max(1, task.simulated)
  const valid = Number.isInteger(count) && count >= least && count <= MAX_SIMULATIONS
  const change = useMutation({
    meta: { inline: true },
    mutationFn: () => labTasks.change(task.id, { cores, simulations: count }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lab-tasks'] })
      onClose()
    },
  })

  return (
    <Dialog
      open
      onOpenChange={(isOpen) => !isOpen && onClose()}
      title="Edit Task"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!valid}
            loading={change.isPending}
            onClick={() => change.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Fieldset legend="Cores">
          <Segmented
            label="Cores"
            items={Array.from({ length: slots }, (_, i) => ({ value: i + 1, label: i + 1 }))}
            value={cores}
            onChange={setCores}
          />
        </Fieldset>
        <Field
          label="Simulations"
          hint={
            task.simulated > 0 && (
              <>
                <span className="num">{fmt.int(task.simulated)}</span> simulated so far: the least
                it can be set to
              </>
            )
          }
        >
          <Input
            type="number"
            min={least}
            max={MAX_SIMULATIONS}
            step={1}
            className="w-40"
            value={simulations}
            onChange={(e) => setSimulations(e.target.value)}
          />
        </Field>
        {change.isError && <ErrorNotice error={change.error} title="Could not change the task" />}
      </div>
    </Dialog>
  )
}

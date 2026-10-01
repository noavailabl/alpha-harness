/** Providers as cards: pick one, add its API key in a popup, then set up a model for it. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ExternalLinkIcon, PlusIcon, RefreshCwIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { fmt } from '@/lib/format'
import {
  type ClaudeUsage,
  type CodexUsage,
  type LLMKey,
  type LLMProvider,
  llm,
} from '@/screens/ai/api'
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Input,
  Metric,
  Notice,
  Panel,
  Progress,
  Skeleton,
} from '@/ui/kit'
import { Dialog } from '@/ui/overlay'
import { ModelSetupForm } from './models'
import {
  useClaude,
  useClaudeUsage,
  useCodex,
  useCodexUsage,
  useInvalidateKeys,
  useKeys,
  useModels,
  useProviders,
} from './shared'

/** Enough to work with for a day, small enough that forgetting it is not expensive. */
const DEFAULT_CAP = '250'

export function Providers() {
  const providers = useProviders()
  const models = useModels()
  const keys = useKeys()
  const codex = useCodex()
  const codexUsage = useCodexUsage()
  const claude = useClaude()
  const claudeUsage = useClaudeUsage()
  const [adding, setAdding] = useState<LLMProvider | null>(null)

  if (providers.isError)
    return <ErrorNotice title="Could not load the providers" error={providers.error} />
  if (!providers.data) return <Skeleton className="h-64" />

  const { data } = providers
  if (data.providers.length === 0) {
    return (
      <Panel>
        <Empty title="The backend lists no providers" />
      </Panel>
    )
  }

  const free = data.providers.filter((p) => !p.paid)
  const paid = data.providers.filter((p) => p.paid)

  const card = (p: LLMProvider) => {
    const keyCount = keys.data?.keys.filter((k) => k.provider === p.id).length ?? 0
    const modelCount = (models.data?.models ?? []).filter((m) => m.provider === p.id).length
    return (
      <button
        key={p.id}
        type="button"
        onClick={() => setAdding(p)}
        className="group flex min-h-32 flex-col justify-between gap-4 rounded-lg border border-hairline bg-surface-1 p-4 text-left transition-colors hover:border-hairline-strong hover:bg-surface-2"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-1.5">
            <span className="text-title text-ink">{p.label}</span>
            {p.id === data.default && <Badge className="w-fit">Recommended</Badge>}
            {p.paid && (
              <Badge tone="outline" className="w-fit">
                Billed to you
              </Badge>
            )}
          </div>
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-hairline text-ink-subtle transition-colors group-hover:border-hairline-strong group-hover:text-ink">
            <PlusIcon className="size-4" />
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-body-compact">
          {keys.isPending ? null : keyCount > 0 ? (
            <Badge>
              <span className="num">{fmt.int(keyCount)}</span> {keyCount === 1 ? 'Key' : 'Keys'}
            </Badge>
          ) : (
            <span className="text-ink-subtle">No key yet</span>
          )}
          {modelCount > 0 ? (
            <span className="text-ink-subtle">
              <span className="num">{fmt.int(modelCount)}</span>{' '}
              {modelCount === 1 ? 'model' : 'models'} set up
            </span>
          ) : (
            keyCount > 0 && models.isSuccess && <Badge tone="warn">No model set up</Badge>
          )}
        </div>
      </button>
    )
  }

  return (
    <>
      <Panel title="Codex with ChatGPT">
        {codex.isPending ? (
          <Skeleton className="h-10" />
        ) : codex.isError ? (
          <ErrorNotice title="Could not check Codex" error={codex.error} />
        ) : codex.data.connected ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-body">
              <Badge>Connected</Badge>
              <span className="text-ink-muted">
                Uses your ChatGPT sign-in · GPT reasoning is fixed at Medium
              </span>
            </div>
            {codexUsage.isPending ? (
              <Skeleton className="h-24" />
            ) : codexUsage.isError ? (
              <ErrorNotice title="Could not load Codex usage" error={codexUsage.error} />
            ) : (
              <CodexUsageCard usage={codexUsage.data} />
            )}
          </div>
        ) : (
          <Notice tone="warn" title="Codex is not signed in with ChatGPT">
            Sign in with Codex on this computer, then reload this page.
          </Notice>
        )}
      </Panel>
      <Panel title="Claude with your Claude subscription">
        {claude.isPending ? (
          <Skeleton className="h-10" />
        ) : claude.isError ? (
          <ErrorNotice title="Could not check Claude" error={claude.error} />
        ) : claude.data.connected ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-body">
              <Badge>Connected</Badge>
              {claude.data.plan && <Badge tone="outline">{claude.data.plan.toUpperCase()}</Badge>}
              <span className="text-ink-muted">
                Uses your Claude Code sign-in{claude.data.email ? ` (${claude.data.email})` : ''} ·
                Claude effort is fixed at Medium
              </span>
            </div>
            {claudeUsage.isPending ? (
              <Skeleton className="h-24" />
            ) : claudeUsage.isError ? (
              <ErrorNotice title="Could not load Claude usage" error={claudeUsage.error} />
            ) : (
              <ClaudeUsageCard usage={claudeUsage.data} />
            )}
          </div>
        ) : (
          <Notice tone="warn" title="Claude is not signed in with a Claude subscription">
            {claude.data.installed
              ? 'Run `claude auth login` in PowerShell, choose your Claude account, then reload this page.'
              : 'Install Claude Code, run `claude auth login` in PowerShell, then reload this page.'}
          </Notice>
        )}
      </Panel>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">{free.map(card)}</div>
      {/* Its own heading, below the free ones, because the default has to keep reading as
          "no card required" even once these exist. */}
      {paid.length > 0 && (
        <section className="flex flex-col gap-3 border-hairline border-t pt-5">
          <div className="flex flex-col gap-1">
            <h3 className="text-title text-ink">Bring Your Own Key</h3>
            <p className="text-body-compact text-pretty text-ink-subtle">{data.paidNote}</p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {paid.map(card)}
          </div>
        </section>
      )}
      <AddKeyDialog provider={adding} onClose={() => setAdding(null)} />
    </>
  )
}

function UsageWindow({
  label,
  window,
}: {
  label: string
  window: NonNullable<CodexUsage['primary']>
}) {
  const reset = window.resetsAt
    ? fmt.dateTime(new Date(window.resetsAt * 1000).toISOString())
    : null
  return (
    <div className="flex flex-col gap-2 rounded-md border border-hairline bg-canvas p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-body font-medium text-ink">{label}</span>
        <span className="num text-body-compact text-ink-muted">
          {fmt.int(window.remainingPercent)}% left
        </span>
      </div>
      <Progress value={window.remainingPercent / 100} label={`${label} allowance remaining`} />
      <span className="text-body-compact text-ink-subtle">
        {fmt.int(window.usedPercent)}% used{reset ? ` · resets ${reset}` : ''}
      </span>
    </div>
  )
}

function CodexUsageCard({ usage }: { usage: CodexUsage }) {
  return (
    <div className="flex flex-col gap-3">
      {usage.error && <Notice tone="warn" title={usage.error} />}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {usage.primary && <UsageWindow label="5-hour allowance" window={usage.primary} />}
        {usage.secondary && <UsageWindow label="Weekly allowance" window={usage.secondary} />}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric boxed label="Plan" value={usage.plan?.toUpperCase() ?? '—'} size="sm" />
        <Metric boxed label="Harness calls" value={fmt.int(usage.localCalls)} size="sm" />
        <Metric
          boxed
          label="Recorded tokens"
          value={fmt.compact(usage.localTokens)}
          hint={usage.localTokensComplete ? undefined : 'Recent Power Pool calls only'}
          size="sm"
        />
        <Metric
          boxed
          label="Extra credits"
          value={usage.hasCredits ? (usage.creditsBalance ?? 'Available') : 'None'}
          size="sm"
        />
      </div>
      <p className="text-body-compact text-pretty text-ink-subtle">
        Account allowance is shared with Codex and other eligible agent features. Harness calls and
        tokens count only requests saved by Alpha Harness.
      </p>
    </div>
  )
}

function ClaudeUsageCard({ usage }: { usage: ClaudeUsage }) {
  const queryClient = useQueryClient()
  const check = useMutation({
    mutationFn: () => llm.claudeUsage(true),
    onSuccess: (fresh) => queryClient.setQueryData(['ai', 'claude', 'usage'], fresh),
  })
  const seen = usage.observedAt
    ? fmt.dateTime(new Date(usage.observedAt * 1000).toISOString())
    : null
  return (
    <div className="flex flex-col gap-3">
      {usage.error && <Notice tone="warn" title={usage.error} />}
      {usage.status === 'rejected' && (
        <Notice tone="warn" title="Claude reports the plan's limit is reached until the reset." />
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {usage.fiveHour && (
          <UsageWindow
            label="5-hour allowance"
            window={{ ...usage.fiveHour, windowMinutes: 300 }}
          />
        )}
        {usage.sevenDay && (
          <UsageWindow
            label="Weekly allowance"
            window={{ ...usage.sevenDay, windowMinutes: 10_080 }}
          />
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric boxed label="Harness calls" value={fmt.int(usage.localCalls)} size="sm" />
        <Metric
          boxed
          label="Recorded tokens"
          value={fmt.compact(usage.localTokens)}
          hint={usage.localTokensComplete ? undefined : 'Recent Power Pool calls only'}
          size="sm"
        />
        <Metric boxed label="Extra usage" value={usage.usingOverage ? 'In use' : 'Off'} size="sm" />
        <Metric boxed label="Last reading" value={seen ?? '—'} size="sm" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" loading={check.isPending} onClick={() => check.mutate()}>
          <RefreshCwIcon />
          Check allowance now
        </Button>
        <p className="max-w-prose text-body-compact text-pretty text-ink-subtle">
          The allowance is shared with Claude, Claude Code and Cowork. It updates after every
          Harness call; checking now spends one tiny Haiku request.
        </p>
      </div>
      {check.isError && <ErrorNotice title="Could not check the allowance" error={check.error} />}
    </div>
  )
}

/**
 * Opens for one provider. The key is cleared as soon as it is sent; only its last characters
 * come back. Adding it leads straight on to setting up a model, which the Key is useless without.
 */
function AddKeyDialog({
  provider,
  onClose,
}: {
  provider: LLMProvider | null
  onClose: () => void
}) {
  const invalidate = useInvalidateKeys()
  const models = useModels()
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [cap, setCap] = useState(DEFAULT_CAP)
  const [added, setAdded] = useState<LLMKey | null>(null)
  // Remounts the model form after each save, so the next model starts from a blank form.
  const [saves, setSaves] = useState(0)
  const setUp = (models.data?.models ?? []).filter((m) => m.provider === provider?.id)

  const paid = provider?.paid ?? false
  const capped = Number.parseInt(cap, 10)
  // A paid key is refused by the backend without one, so the button should not offer to try.
  const capReady = !paid || (Number.isFinite(capped) && capped >= 1)

  const add = useMutation({
    mutationFn: (p: LLMProvider) =>
      llm.addKey({
        key: key.trim(),
        label: label.trim() || null,
        provider: p.id,
        daily_limit: p.paid ? capped : null,
      }),
    onSuccess: (result, p) => {
      setAdded(result)
      setKey('')
      setLabel('')
      toast.success(`Added ${p.label} Key ${result.hint}`)
      invalidate()
    },
  })

  const reset = () => {
    setKey('')
    setLabel('')
    setCap(DEFAULT_CAP)
    setAdded(null)
    setSaves(0)
    add.reset()
  }
  const close = () => {
    reset()
    onClose()
  }

  return (
    <Dialog
      open={provider !== null}
      onOpenChange={(open) => !open && close()}
      className="max-w-2xl"
      title={provider ? `Add a ${provider.label} Key` : 'Add a Key'}
      footer={
        added ? (
          <>
            <Button variant="ghost" onClick={reset}>
              Add another Key
            </Button>
            <Button variant={setUp.length > 0 ? 'primary' : 'ghost'} onClick={close}>
              {setUp.length > 0 ? 'Done' : 'Set up later'}
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="llm-add-key"
              loading={add.isPending}
              disabled={!key.trim() || !capReady}
            >
              Add Key
            </Button>
          </>
        )
      }
    >
      {provider &&
        (added ? (
          <div className="flex flex-col gap-4">
            <Notice title="Key added">
              {provider.label} Key <span className="num text-ink">{added.hint}</span>
              {added.label && <> labelled “{added.label}”</>} is in the pool.{' '}
              {setUp.length > 0 ? (
                <>
                  Set up for {provider.label}:{' '}
                  <span className="num text-ink">{setUp.map((m) => m.id).join(', ')}</span>. This
                  Key gets the same limits. Add another model below, or you are done.
                </>
              ) : (
                'Next, set up a model for it: the assistant cannot use the Key without one.'
              )}
            </Notice>
            <h3 className="text-title text-ink">Set up a model</h3>
            <ModelSetupForm
              key={saves}
              provider={provider}
              onSaved={() => setSaves((n) => n + 1)}
            />
          </div>
        ) : (
          <form
            id="llm-add-key"
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (key.trim()) add.mutate(provider)
            }}
          >
            {provider.paid && <Notice tone="warn">{provider.tierNote}</Notice>}
            <Button
              variant="secondary"
              className="w-fit"
              render={<a href={provider.onboardingUrl} target="_blank" rel="noopener noreferrer" />}
            >
              {provider.paid ? `Get a ${provider.label} Key` : `Get a free ${provider.label} Key`}
              <ExternalLinkIcon />
            </Button>
            <Field label="API Key">
              <Input
                type="password"
                autoComplete="off"
                spellCheck={false}
                className="num"
                placeholder={provider.keyHint}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </Field>
            <Field label="Label (optional)">
              <Input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. personal account"
                autoComplete="off"
              />
            </Field>
            {provider.paid && (
              <Field
                label="Daily request cap"
                hint="Alpha Harness stops at this many requests a day on this key, and starts again when each model's day resets. You can change it later."
              >
                <Input
                  type="number"
                  min={1}
                  step={1}
                  value={cap}
                  onChange={(e) => setCap(e.target.value)}
                  aria-invalid={!capReady}
                />
              </Field>
            )}
          </form>
        ))}
    </Dialog>
  )
}

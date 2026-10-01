/**
 * LLM Integration (CLAUDE.md §4.6), provider first: until a key exists the page is only the
 * provider grid, and only then do the tabs appear (`/ai/$tab`, `/ai/assistant/$threadId`).
 */

import { Link, useParams } from '@tanstack/react-router'
import { AI_TABS } from '@/shell/nav'
import {
  Empty,
  ErrorNotice,
  LINK,
  Page,
  PageHeader,
  Panel,
  Skeleton,
  TabBar,
  TabLink,
} from '@/ui/kit'
import { Assistant } from './assistant'
import { Budget } from './budget'
import { Keys } from './keys'
import { Prompts } from './prompts'
import { Providers } from './providers'
import { useClaude, useCodex, useKeys } from './shared'

const SCREENS = { keys: Keys, budget: Budget, prompts: Prompts } as const

export function AiScreen() {
  const params = useParams({ strict: false })
  const keys = useKeys()
  const codex = useCodex()
  const claude = useClaude()
  const threadId = params.threadId ? Number(params.threadId) || null : null
  const tab = params.threadId ? 'assistant' : params.tab
  const Screen = tab && tab in SCREENS ? SCREENS[tab as keyof typeof SCREENS] : null
  const hasAssistant =
    (keys.data?.keys.length ?? 0) > 0 ||
    codex.data?.connected === true ||
    claude.data?.connected === true

  // Every branch keeps its slot, so the provider grid (and its open popup) stays mounted when
  // the first key lands and the tab bar appears above it.
  return (
    <Page>
      <PageHeader title="LLM Integration" />
      {keys.isError && <ErrorNotice title="Could not load the Keys" error={keys.error} />}
      {hasAssistant && (
        <TabBar>
          {AI_TABS.map((t) => (
            <TabLink key={t.tab} to="/ai/$tab" params={{ tab: t.tab }}>
              {t.label}
            </TabLink>
          ))}
        </TabBar>
      )}
      {keys.isPending || codex.isPending || claude.isPending ? (
        <Skeleton className="h-64" />
      ) : !hasAssistant || tab === 'providers' ? (
        <Providers />
      ) : tab === 'assistant' ? (
        <Assistant threadId={threadId} />
      ) : Screen ? (
        <Screen />
      ) : (
        <Panel>
          <Empty title={`There is no “${tab}” tab`}>
            <Link to="/ai/$tab" params={{ tab: 'providers' }} className={LINK}>
              Go to Providers
            </Link>
          </Empty>
        </Panel>
      )}
    </Page>
  )
}

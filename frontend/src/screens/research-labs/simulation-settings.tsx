/** The settings a task holds at one value for every simulation, laid out as BRAIN lays them out. */

import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Field, Fieldset, Input, Segmented } from '@/ui/kit'
import { Select } from '@/ui/overlay'
import { TruncationAgentInfo } from './truncation-agent-info'

type OnOff = 'ON' | 'OFF'

const ON_OFF: { value: OnOff; label: string }[] = [
  { value: 'ON', label: 'On' },
  { value: 'OFF', label: 'Off' },
]

/** Each setting is its own outlined block, styled like the boxed metrics beside it. */
const TILE = 'rounded-md border border-hairline-strong bg-surface-2 p-3'

/** A typed number held inside BRAIN's own bounds. */
export const clamp = (text: string, max: number) =>
  Math.min(max, Math.max(0, Math.round(Number(text) || 0)))

/** BRAIN's `P{years}Y{months}M0D`, from the two boxes. */
export const testPeriodOf = (years: string, months: string) =>
  `P${clamp(years, 6)}Y${clamp(months, 11)}M0D`

/**
 * Decay, Truncation, NaN Handling and Test Period — Test Period in years *and* months, as BRAIN
 * has it. Pasteurization shows only where it is passed.
 */
export function SimulationSettingsFields({
  decay,
  setDecay,
  truncation,
  setTruncation,
  truncationAgent = false,
  setTruncationAgent,
  agentSummary,
  pasteurization,
  setPasteurization,
  nanHandling,
  setNanHandling,
  testYears,
  setTestYears,
  testMonths,
  setTestMonths,
}: {
  decay: string
  setDecay: (v: string) => void
  truncation: string
  setTruncation: (v: string) => void
  /** Offers the Truncation Agent beside a single value; shown only where it is passed. */
  truncationAgent?: boolean | undefined
  setTruncationAgent?: ((v: boolean) => void) | undefined
  /** What the agent will set, said in place of the value box while it is on. */
  agentSummary?: ReactNode
  pasteurization?: OnOff | undefined
  setPasteurization?: ((v: OnOff) => void) | undefined
  nanHandling: OnOff
  setNanHandling: (v: OnOff) => void
  testYears: string
  setTestYears: (v: string) => void
  testMonths: string
  setTestMonths: (v: string) => void
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-1 gap-4 sm:grid-cols-2',
        pasteurization && setPasteurization ? 'xl:grid-cols-5' : 'xl:grid-cols-4',
      )}
    >
      <Field label="Decay" className={TILE}>
        <Input
          type="number"
          min={0}
          max={512}
          step={1}
          value={decay}
          onChange={(e) => setDecay(e.target.value)}
        />
      </Field>
      <Field label="Truncation" className={TILE}>
        <div className="flex flex-col gap-2">
          {/* The value sits beside the mode switch, so Truncation stays one line tall like the
              fields around it; only the agent's summary drops below. */}
          <div className="flex items-center gap-2">
            {setTruncationAgent && (
              <div className="shrink-0">
                <Segmented
                  label="Truncation"
                  items={[
                    { value: 'single', label: 'Single' },
                    { value: 'agent', label: 'Agent' },
                  ]}
                  value={truncationAgent ? 'agent' : 'single'}
                  onChange={(v) => setTruncationAgent(v === 'agent')}
                />
              </div>
            )}
            {!truncationAgent && (
              <Input
                type="number"
                min={0}
                max={1}
                step={0.01}
                aria-label="Truncation"
                className="min-w-0 flex-1"
                value={truncation}
                onChange={(e) => setTruncation(e.target.value)}
              />
            )}
            {setTruncationAgent && (
              <div className="shrink-0">
                <TruncationAgentInfo />
              </div>
            )}
          </div>
          {truncationAgent && <p className="text-body-compact text-ink-muted">{agentSummary}</p>}
        </div>
      </Field>
      {pasteurization && setPasteurization && (
        <Field label="Pasteurization" className={TILE}>
          <Select
            label="Pasteurization"
            value={pasteurization}
            onChange={setPasteurization}
            items={ON_OFF}
          />
        </Field>
      )}
      <Field label="NaN Handling" className={TILE}>
        <Select label="NaN Handling" value={nanHandling} onChange={setNanHandling} items={ON_OFF} />
      </Field>
      {/* BRAIN takes P0Y0M0D up to P6Y0M0D, and its own form splits the two. The units sit
          beside the boxes rather than above them, so this reads as one control on one line
          and its inputs share a baseline with Decay and Truncation. */}
      <Fieldset legend="Test Period" className={TILE}>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={6}
            step={1}
            aria-label="Test period, years"
            className="min-w-0 flex-1"
            value={testYears}
            onChange={(e) => setTestYears(e.target.value)}
          />
          <span className="shrink-0 text-body-compact text-ink-subtle">Years</span>
          <Input
            type="number"
            min={0}
            max={11}
            step={1}
            aria-label="Test period, months"
            className="min-w-0 flex-1"
            value={testMonths}
            onChange={(e) => setTestMonths(e.target.value)}
          />
          <span className="shrink-0 text-body-compact text-ink-subtle">Months</span>
        </div>
      </Fieldset>
    </div>
  )
}

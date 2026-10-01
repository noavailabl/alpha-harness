/** Picking datasets in the Data Explorer's Fields filters for a lab. */

import { createPick } from '@/lib/pick'
import type { FieldFilterState } from '@/screens/data/state'

export type PickFrom = '/labs/search' | '/labs/template' | '/labs/power-pool'

/** The ids are the datasets; the extra is the filter they were chosen under, which the lab
 *  applies to their fields. */
export const useDatasetPick = createPick<PickFrom, FieldFilterState | null>(
  'alpha-harness-dataset-pick',
  '/labs/search',
)

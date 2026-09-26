import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ChartAdvisorState, WorkspaceState } from '../api/types'

/**
 * ENH-05 — durable UI state, keyed by dataset, held in the query cache and
 * mirrored to the backend.
 *
 * Navigating away and back reads the cached value with no refetch and no
 * re-analysis. A deliberate browser refresh restores the last persisted value
 * from the dataset cache. Because the state is stored per dataset id on the
 * server and the query key includes the dataset id, state from one file can
 * never appear under another.
 */

const SAVE_DEBOUNCE_MS = 400

export const emptyChartAdvisorState: ChartAdvisorState = {
  selected_column_ids: [],
  active_chart_type: null,
  question: '',
  show_rejected: true,
  scroll_y: 0,
}

export function workspaceStateKey(datasetId: string): (string | undefined)[] {
  return ['workspace-state', datasetId]
}

export function useWorkspaceState(datasetId: string) {
  const queryClient = useQueryClient()
  const key = workspaceStateKey(datasetId)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const query = useQuery({
    queryKey: key,
    queryFn: () => api.workspaceState(datasetId),
    enabled: Boolean(datasetId),
    // Durable state: once loaded it is authoritative for the session, so
    // remounting a page never triggers a refetch.
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  })

  const save = useMutation({
    mutationFn: (state: WorkspaceState) => api.saveWorkspaceState(datasetId, state),
    onSuccess: (state) => queryClient.setQueryData(key, state),
  })

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const state = useMemo<WorkspaceState | undefined>(
    () => (query.data?.dataset_id === datasetId ? query.data : undefined),
    [query.data, datasetId],
  )

  /** Optimistic local update, then a debounced write so typing is not chatty. */
  const patch = useCallback(
    (changes: Partial<WorkspaceState>) => {
      const current =
        queryClient.getQueryData<WorkspaceState>(key) ??
        ({
          dataset_id: datasetId,
          version: 1,
          chart_advisor: emptyChartAdvisorState,
          ask: { last_question: '', last_quick_ask_id: null },
          recommendations_tier: 'easy',
          profile_role_filter: 'all',
          schema_fingerprint: '',
          updated_at: null,
          dropped_on_restore: [],
        } satisfies WorkspaceState)

      const next: WorkspaceState = { ...current, ...changes, dataset_id: datasetId }
      queryClient.setQueryData(key, next)

      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => save.mutate(next), SAVE_DEBOUNCE_MS)
      return next
    },
    [datasetId, key, queryClient, save],
  )

  const patchChartAdvisor = useCallback(
    (changes: Partial<ChartAdvisorState>) => {
      const current =
        queryClient.getQueryData<WorkspaceState>(key)?.chart_advisor ?? emptyChartAdvisorState
      return patch({ chart_advisor: { ...current, ...changes } })
    },
    [key, patch, queryClient],
  )

  return {
    state,
    chartAdvisor: state?.chart_advisor ?? emptyChartAdvisorState,
    isLoading: query.isLoading,
    error: query.error,
    droppedOnRestore: state?.dropped_on_restore ?? [],
    patch,
    patchChartAdvisor,
    isSaving: save.isPending,
  }
}

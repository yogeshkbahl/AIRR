import { useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ChartSpec, EvidenceKind, Storyboard, StoryboardItem } from '../api/types'

const newId = () => `item-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`

export function useStoryboard(datasetId: string) {
  const queryClient = useQueryClient()
  const key = ['storyboard', datasetId]

  const query = useQuery({ queryKey: key, queryFn: () => api.storyboard(datasetId) })

  const save = useMutation({
    mutationFn: (board: Storyboard) => api.saveStoryboard(datasetId, board),
    onSuccess: (board) => queryClient.setQueryData(key, board),
  })

  const current = useCallback(
    async (): Promise<Storyboard> =>
      queryClient.getQueryData<Storyboard>(key) ??
      (await queryClient.fetchQuery({ queryKey: key, queryFn: () => api.storyboard(datasetId) })),
    [datasetId, key, queryClient],
  )

  const addChart = useCallback(
    async (spec: ChartSpec, options?: { title?: string; description?: string; evidence?: EvidenceKind }) => {
      const board = await current()
      const item: StoryboardItem = {
        id: newId(),
        title: options?.title ?? spec.title,
        description: options?.description ?? '',
        kind: spec.chart_type === 'kpi_card' ? 'kpi' : spec.chart_type === 'table' ? 'table' : 'chart',
        spec,
        text: null,
        evidence_kind: options?.evidence ?? 'suggestion',
        order: board.items.length,
      }
      return save.mutateAsync({ ...board, items: [...board.items, item] })
    },
    [current, save],
  )

  const addInsight = useCallback(
    async (title: string, text: string, evidence: EvidenceKind = 'inference') => {
      const board = await current()
      const item: StoryboardItem = {
        id: newId(),
        title,
        description: '',
        kind: 'insight',
        spec: null,
        text,
        evidence_kind: evidence,
        order: board.items.length,
      }
      return save.mutateAsync({ ...board, items: [...board.items, item] })
    },
    [current, save],
  )

  const update = useCallback(
    async (patch: Partial<Storyboard>) => {
      const board = await current()
      return save.mutateAsync({ ...board, ...patch })
    },
    [current, save],
  )

  const removeItem = useCallback(
    async (id: string) => {
      const board = await current()
      return save.mutateAsync({ ...board, items: board.items.filter((item) => item.id !== id) })
    },
    [current, save],
  )

  const moveItem = useCallback(
    async (id: string, direction: -1 | 1) => {
      const board = await current()
      const items = [...board.items]
      const index = items.findIndex((item) => item.id === id)
      const target = index + direction
      if (index < 0 || target < 0 || target >= items.length) return board
      ;[items[index], items[target]] = [items[target], items[index]]
      return save.mutateAsync({ ...board, items: items.map((item, order) => ({ ...item, order })) })
    },
    [current, save],
  )

  return {
    board: query.data,
    isLoading: query.isLoading,
    error: query.error,
    isSaving: save.isPending,
    saveError: save.error,
    addChart,
    addInsight,
    update,
    removeItem,
    moveItem,
  }
}

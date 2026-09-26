import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { DatasetOverview } from '../api/types'

/**
 * ENH-02 — the single selector behind the four summary tiles.
 *
 * Overview and Data Profile both read this, so Total Rows, Total Columns,
 * Duplicate Rows and Missing Cells cannot drift between the two pages. The
 * query key is dataset-scoped and the returned data is discarded if it belongs
 * to another dataset, so a stale payload can never be shown under a new file.
 */
export function useDatasetSummary(datasetId: string) {
  const query = useQuery({
    queryKey: ['overview', datasetId],
    queryFn: () => api.overview(datasetId),
    enabled: Boolean(datasetId),
  })

  const matches = query.data?.dataset_id === datasetId
  const overview: DatasetOverview | undefined = matches ? query.data : undefined

  return {
    overview,
    isLoading: query.isLoading || (Boolean(query.data) && !matches),
    error: query.error,
    refetch: query.refetch,
  }
}

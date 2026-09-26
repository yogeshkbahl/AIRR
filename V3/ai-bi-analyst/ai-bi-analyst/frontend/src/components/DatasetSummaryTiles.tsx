import { Alert, Grid, Skeleton, Tooltip } from '@mui/material'
import { useDatasetSummary } from '../hooks/useDatasetSummary'
import { KeyFigure } from './Bits'
import { compactNumber, percent } from '../utils/format'

/**
 * ENH-02 — Total Rows, Total Columns, Duplicate Rows, Missing Cells.
 *
 * Rendered from `useDatasetSummary`, which both Overview and Data Profile call,
 * so the two pages are reading the same authoritative object rather than each
 * computing its own version of the same four numbers.
 */
export default function DatasetSummaryTiles({ datasetId }: { datasetId: string }) {
  const { overview, isLoading, error } = useDatasetSummary(datasetId)

  if (isLoading) {
    return (
      <Grid container spacing={2}>
        {[0, 1, 2, 3].map((index) => (
          <Grid item xs={6} md={3} key={index}>
            <Skeleton variant="rectangular" height={104} />
          </Grid>
        ))}
      </Grid>
    )
  }

  if (error || !overview) {
    return (
      <Alert severity="warning">
        The dataset summary is unavailable, so these tiles are blank rather than showing numbers from another
        file. Reload the page to try again.
      </Alert>
    )
  }

  const cells = overview.analyzed_row_count * Math.max(1, overview.column_count)

  return (
    <Grid container spacing={2}>
      <Grid item xs={6} md={3}>
        <Tooltip title={overview.sampled ? 'Counted over the whole file, not the analysed sample.' : ''}>
          <span>
            <KeyFigure
              label="Total rows"
              value={overview.row_count.toLocaleString()}
              hint={overview.sampled ? `${overview.analyzed_row_count.toLocaleString()} analysed` : undefined}
            />
          </span>
        </Tooltip>
      </Grid>
      <Grid item xs={6} md={3}>
        <KeyFigure label="Total columns" value={overview.column_count.toLocaleString()} />
      </Grid>
      <Grid item xs={6} md={3}>
        <KeyFigure
          label="Duplicate rows"
          value={overview.duplicate_row_count.toLocaleString()}
          hint={`${percent(overview.duplicate_row_percent, 2)} of ${overview.analyzed_row_count.toLocaleString()} rows, matched on every column`}
          tone={overview.duplicate_row_percent > 0 ? 'warn' : 'ink'}
        />
      </Grid>
      <Grid item xs={6} md={3}>
        <KeyFigure
          label="Missing cells"
          value={compactNumber(overview.missing_cell_count)}
          hint={`${percent(overview.missing_cell_percent, 2)} of ${cells.toLocaleString()} cells; nulls only, blank strings are reported separately`}
          tone={overview.missing_cell_percent > 5 ? 'warn' : 'ink'}
        />
      </Grid>
    </Grid>
  )
}

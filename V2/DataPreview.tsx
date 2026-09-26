import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert, Box, Button, FormControl, IconButton, InputLabel, MenuItem, Paper, Select, Stack, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import { api } from '../api/client'
import type { ColumnProfile } from '../api/types'
import { ErrorView, Loading } from './Bits'
import { cellText } from '../utils/format'
import { palette } from '../theme'

/**
 * The rows themselves, paginated and sortable, with sensitive columns masked by
 * the backend before they are sent. The filter uses the same operator
 * allow-list as the governed query plan, so this view cannot become a way to
 * run an arbitrary expression, and filtering on a personal-data column is
 * refused by the server rather than hidden in the UI.
 */

const PAGE_SIZE = 25

const OPERATORS: { value: string; label: string }[] = [
  { value: 'eq', label: 'is / contains' },
  { value: 'neq', label: 'is not' },
  { value: 'gt', label: 'greater than' },
  { value: 'gte', label: 'at least' },
  { value: 'lt', label: 'less than' },
  { value: 'lte', label: 'at most' },
  { value: 'is_null', label: 'is empty' },
  { value: 'not_null', label: 'is not empty' },
]

export default function DataPreview({
  datasetId,
  columns,
}: {
  datasetId: string
  columns: ColumnProfile[]
}) {
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState<string | null>(null)
  const [sortDesc, setSortDesc] = useState(false)
  const [filterColumn, setFilterColumn] = useState('')
  const [filterOp, setFilterOp] = useState('eq')
  const [filterValue, setFilterValue] = useState('')
  const [appliedFilter, setAppliedFilter] = useState<{ column: string; op: string; value: string } | null>(null)

  const filterable = useMemo(() => columns.filter((column) => !column.is_sensitive), [columns])
  const labels = useMemo(() => new Map(columns.map((column) => [column.name, column.label])), [columns])

  const request = {
    offset,
    limit: PAGE_SIZE,
    sort_by: sortBy,
    sort_desc: sortDesc,
    filter_column: appliedFilter?.column ?? null,
    filter_op: appliedFilter?.op ?? null,
    filter_value: appliedFilter?.value ?? null,
  }

  const preview = useQuery({
    queryKey: ['preview', datasetId, request],
    queryFn: () => api.preview(datasetId, request),
    staleTime: 60_000,
  })

  const data = preview.data
  const needsValue = !['is_null', 'not_null'].includes(filterOp)

  const applyFilter = () => {
    setOffset(0)
    setAppliedFilter(
      filterColumn ? { column: filterColumn, op: filterOp, value: needsValue ? filterValue : '' } : null,
    )
  }

  const toggleSort = (column: string) => {
    setOffset(0)
    if (sortBy === column) {
      setSortDesc((value) => !value)
      return
    }
    setSortBy(column)
    setSortDesc(false)
  }

  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mb: 1.5 }}>
        <Typography variant="h3">Data preview</Typography>
        <Typography variant="caption">
          {data
            ? `rows ${data.returned_rows ? data.offset + 1 : 0}–${data.offset + data.returned_rows} of ${
                data.filtered_rows.toLocaleString()
              }${data.filtered_rows !== data.total_rows ? ` filtered from ${data.total_rows.toLocaleString()}` : ''}`
            : 'loading…'}
        </Typography>
      </Stack>

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ mb: 1.5 }} alignItems={{ md: 'center' }}>
        <FormControl size="small" sx={{ minWidth: 190 }}>
          <InputLabel id="filter-column">Filter column</InputLabel>
          <Select
            labelId="filter-column"
            label="Filter column"
            value={filterColumn}
            onChange={(event) => setFilterColumn(event.target.value)}
          >
            <MenuItem value="">No filter</MenuItem>
            {filterable.map((column) => (
              <MenuItem key={column.name} value={column.name}>
                {column.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 150 }} disabled={!filterColumn}>
          <InputLabel id="filter-op">Condition</InputLabel>
          <Select
            labelId="filter-op"
            label="Condition"
            value={filterOp}
            onChange={(event) => setFilterOp(event.target.value)}
          >
            {OPERATORS.map((operator) => (
              <MenuItem key={operator.value} value={operator.value}>
                {operator.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField
          size="small"
          label="Value"
          value={filterValue}
          disabled={!filterColumn || !needsValue}
          onChange={(event) => setFilterValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') applyFilter()
          }}
        />
        <Button onClick={applyFilter} disabled={!filterColumn}>
          Apply
        </Button>
        {appliedFilter && (
          <Button
            onClick={() => {
              setAppliedFilter(null)
              setFilterColumn('')
              setFilterValue('')
              setOffset(0)
            }}
          >
            Clear
          </Button>
        )}
      </Stack>

      {preview.isLoading && <Loading label="Fetching rows…" />}
      {preview.error ? (
        <ErrorView
          error={preview.error}
          action={
            <Button size="small" onClick={() => setAppliedFilter(null)}>
              Clear filter
            </Button>
          }
        />
      ) : null}

      {data && (
        <>
          <TableContainer sx={{ maxHeight: 420, border: `1px solid ${palette.line}` }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  {data.columns.map((column) => (
                    <TableCell
                      key={column}
                      sortDirection={data.sort_by === column ? (data.sort_desc ? 'desc' : 'asc') : false}
                    >
                      <Stack
                        direction="row"
                        spacing={0.25}
                        alignItems="center"
                        sx={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                        onClick={() => toggleSort(column)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') toggleSort(column)
                        }}
                        aria-label={`Sort by ${labels.get(column) ?? column}`}
                      >
                        <span>{labels.get(column) ?? column}</span>
                        {data.sort_by === column &&
                          (data.sort_desc ? (
                            <ArrowDownwardIcon sx={{ fontSize: 13 }} />
                          ) : (
                            <ArrowUpwardIcon sx={{ fontSize: 13 }} />
                          ))}
                        {data.masked_columns.includes(column) && (
                          <Tooltip title="Masked: this column looks like personal data">
                            <Box component="span" sx={{ color: palette.severity.medium, fontSize: '0.7rem' }}>
                              masked
                            </Box>
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {data.rows.map((row, index) => (
                  <TableRow key={index} hover>
                    {data.columns.map((column) => (
                      <TableCell
                        key={column}
                        className={typeof row[column] === 'number' ? 'figure' : undefined}
                        align={typeof row[column] === 'number' ? 'right' : 'left'}
                      >
                        {cellText(row[column])}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {data.rows.length === 0 && (
            <Alert severity="info" sx={{ mt: 1.5 }}>
              No rows match this filter. Clear it or widen the condition.
            </Alert>
          )}

          <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5 }}>
            <IconButton
              size="small"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              aria-label="Previous page"
            >
              ‹
            </IconButton>
            <IconButton
              size="small"
              disabled={offset + PAGE_SIZE >= data.filtered_rows}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              aria-label="Next page"
            >
              ›
            </IconButton>
            {data.note && <Typography variant="caption">{data.note}</Typography>}
          </Stack>
        </>
      )}
    </Paper>
  )
}

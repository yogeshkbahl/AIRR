import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Container, FormControl, InputLabel, MenuItem, Paper, Select, Stack,
  TextField, Typography,
} from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { api } from '../api/client'
import type { HealthResponse } from '../api/types'
import { ErrorView } from '../components/Bits'
import { palette } from '../theme'
import { bytes } from '../utils/format'

const ACCEPTED = '.csv,.tsv,.txt,.xlsx,.xlsm,.parquet'

export default function UploadPage({ limits }: { limits?: HealthResponse['limits'] }) {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [sheets, setSheets] = useState<string[]>([])
  const [sheet, setSheet] = useState<string>('')
  const [context, setContext] = useState('')
  const [dragging, setDragging] = useState(false)

  const upload = useMutation({
    mutationFn: () => api.upload({ file: file!, businessContext: context, sheet: sheet || null }),
    onSuccess: (result) => navigate(`/d/${result.dataset_id}/overview`),
  })

  const accept = useCallback(async (picked: File) => {
    setFile(picked)
    setSheets([])
    setSheet('')
    if (/\.xlsx?$|\.xlsm$/i.test(picked.name)) {
      try {
        const probe = await api.probeSheets(picked)
        setSheets(probe.sheets)
        setSheet(probe.sheets[0] ?? '')
      } catch {
        setSheets([])
      }
    }
  }, [])

  return (
    <Box sx={{ minHeight: '100vh', backgroundColor: palette.canvas }}>
      <Container maxWidth="md" sx={{ py: { xs: 4, md: 8 } }}>
        <Typography sx={{ fontSize: { xs: '2rem', md: '2.6rem' }, fontWeight: 600, letterSpacing: '-0.03em' }}>
          Start from a file you have never seen
        </Typography>
        <Typography variant="body1" color="text.secondary" sx={{ mt: 1.5, maxWidth: '60ch' }}>
          This workbench profiles your data in Python, shows you what is wrong with it, and proposes reports you
          can defend in a review. A language model writes the wording and ranks the ideas. It never invents a
          number, and it never sees your raw rows.
        </Typography>

        <Paper
          sx={{
            mt: 4, p: 4, textAlign: 'center', cursor: 'pointer',
            borderStyle: 'dashed', borderWidth: 2,
            borderColor: dragging ? palette.primary : palette.line,
            backgroundColor: dragging ? palette.primaryTint : palette.surface,
          }}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            const dropped = event.dataTransfer.files?.[0]
            if (dropped) void accept(dropped)
          }}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click()
          }}
          aria-label="Choose a data file"
        >
          <UploadFileIcon sx={{ fontSize: 34, color: palette.primary }} />
          <Typography variant="h3" sx={{ mt: 1 }}>
            {file ? file.name : 'Drop a file here, or click to choose one'}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {file
              ? `${bytes(file.size)} ready to profile`
              : `CSV, TSV, XLSX or Parquet, up to ${limits?.max_upload_mb ?? 200} MB`}
          </Typography>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            hidden
            onChange={(event) => {
              const picked = event.target.files?.[0]
              if (picked) void accept(picked)
            }}
          />
        </Paper>

        {sheets.length > 1 && (
          <FormControl size="small" sx={{ mt: 2, minWidth: 240 }}>
            <InputLabel id="sheet-label">Sheet to analyse</InputLabel>
            <Select
              labelId="sheet-label"
              label="Sheet to analyse"
              value={sheet}
              onChange={(event) => setSheet(event.target.value)}
            >
              {sheets.map((name) => (
                <MenuItem key={name} value={name}>{name}</MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <TextField
          sx={{ mt: 3 }}
          fullWidth
          multiline
          minRows={3}
          label="What is this data, and who reads the report?"
          placeholder="Monthly order extract from the ERP. The regional directors review revenue and margin by channel."
          value={context}
          onChange={(event) => setContext(event.target.value.slice(0, 4000))}
          helperText="Optional, but it changes which reports get suggested. Treated as intent, never as fact about the data."
        />

        <Stack direction="row" spacing={2} sx={{ mt: 3 }} alignItems="center" flexWrap="wrap">
          <Button
            variant="contained"
            size="large"
            disabled={!file || upload.isPending}
            onClick={() => upload.mutate()}
          >
            {upload.isPending ? 'Profiling the file…' : 'Profile this file'}
          </Button>
          {file && (
            <Button
              onClick={() => {
                setFile(null)
                setSheets([])
                setSheet('')
              }}
            >
              Choose a different file
            </Button>
          )}
        </Stack>

        {upload.error && (
          <Box sx={{ mt: 2 }}>
            <ErrorView error={upload.error} />
          </Box>
        )}

        <Alert severity="info" sx={{ mt: 4 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>What leaves this machine</Typography>
          <Typography variant="body2">
            Only a compact profile: column names, inferred roles, summary statistics, cardinality, missingness
            and anomaly counts, plus the context you typed. Raw rows are never sent, values in columns that look
            like personal data are masked, and provider keys stay in the backend environment.
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
            <Chip size="small" variant="outlined" label="No raw rows in prompts" />
            <Chip size="small" variant="outlined" label="Keys server-side only" />
            <Chip
              size="small"
              variant="outlined"
              label={`Sessions deleted after ${limits?.session_retention_hours ?? 24}h`}
            />
          </Stack>
        </Alert>

        <Typography variant="caption" sx={{ display: 'block', mt: 3 }}>
          No sample data to hand? Run <code>python samples/generate_samples.py</code> in the backend folder to
          write two synthetic files with known defects.
        </Typography>
      </Container>
    </Box>
  )
}

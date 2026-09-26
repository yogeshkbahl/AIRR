import { createTheme } from '@mui/material/styles'

/**
 * Visual direction: an instrument panel for people who have to defend the
 * numbers they publish. Cool graphite paper, one institutional blue for
 * navigation and action, and a small fixed signal palette reserved for
 * severity and evidence class, so colour always means something. Figures are
 * set in a mono face with tabular figures so columns of numbers line up.
 */

export const palette = {
  canvas: '#EEF1F4',
  surface: '#FFFFFF',
  sunken: '#F6F8FA',
  ink: '#10171E',
  muted: '#5A6A78',
  line: '#D3DAE1',
  primary: '#1F4E79',
  primaryTint: '#E7EFF6',
  severity: { high: '#A32A1F', medium: '#B25E00', low: '#5A6875', info: '#3F6B8A' },
  evidence: { fact: '#1F4E79', inference: '#17605B', suggestion: '#5B3E8E', assumption: '#B25E00' },
  tier: { easy: '#17605B', medium: '#1F4E79', complex: '#5B3E8E', very_complex: '#8A3A5E' },
} as const

export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: palette.primary, light: palette.primaryTint, contrastText: '#FFFFFF' },
    secondary: { main: palette.evidence.inference },
    error: { main: palette.severity.high },
    warning: { main: palette.severity.medium },
    info: { main: palette.severity.info },
    success: { main: palette.evidence.inference },
    background: { default: palette.canvas, paper: palette.surface },
    text: { primary: palette.ink, secondary: palette.muted },
    divider: palette.line,
  },
  shape: { borderRadius: 4 },
  typography: {
    fontFamily: '"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h1: { fontSize: '1.75rem', fontWeight: 600, letterSpacing: '-0.015em' },
    h2: { fontSize: '1.3rem', fontWeight: 600, letterSpacing: '-0.01em' },
    h3: { fontSize: '1.05rem', fontWeight: 600 },
    h4: { fontSize: '0.95rem', fontWeight: 600 },
    subtitle2: { fontSize: '0.8rem', fontWeight: 500, color: palette.muted },
    body1: { fontSize: '0.9rem', lineHeight: 1.55 },
    body2: { fontSize: '0.82rem', lineHeight: 1.5 },
    caption: { fontSize: '0.75rem', color: palette.muted },
    button: { textTransform: 'none', fontWeight: 500 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: palette.canvas },
        '.figure': {
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontVariantNumeric: 'tabular-nums',
        },
        ':focus-visible': { outline: `2px solid ${palette.primary}`, outlineOffset: 2 },
        '@media (prefers-reduced-motion: reduce)': {
          '*': { animationDuration: '0.01ms !important', transitionDuration: '0.01ms !important' },
        },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { border: `1px solid ${palette.line}`, backgroundImage: 'none' } },
    },
    MuiCard: { defaultProps: { elevation: 0 }, styleOverrides: { root: { border: `1px solid ${palette.line}` } } },
    MuiAppBar: {
      defaultProps: { elevation: 0, color: 'inherit' },
      styleOverrides: { root: { borderBottom: `1px solid ${palette.line}`, backgroundColor: palette.surface } },
    },
    MuiChip: { styleOverrides: { root: { borderRadius: 3, fontWeight: 500 }, sizeSmall: { height: 21 } } },
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: palette.line, fontSize: '0.82rem' },
        head: { backgroundColor: palette.sunken, fontWeight: 600, whiteSpace: 'nowrap' },
      },
    },
    MuiTooltip: { defaultProps: { arrow: true }, styleOverrides: { tooltip: { fontSize: '0.75rem', maxWidth: 320 } } },
    MuiAlert: { styleOverrides: { root: { border: `1px solid ${palette.line}`, alignItems: 'flex-start' } } },
    MuiTab: { styleOverrides: { root: { textTransform: 'none', minHeight: 44, fontWeight: 500 } } },
  },
})

export const severityColor = (severity: string) =>
  palette.severity[severity as keyof typeof palette.severity] ?? palette.severity.info

export const evidenceColor = (kind: string) =>
  palette.evidence[kind as keyof typeof palette.evidence] ?? palette.muted

export const tierColor = (tier: string) => palette.tier[tier as keyof typeof palette.tier] ?? palette.primary

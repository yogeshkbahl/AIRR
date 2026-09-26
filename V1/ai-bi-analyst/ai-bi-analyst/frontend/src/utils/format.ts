export const compactNumber = (value: number | null | undefined): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}bn`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}m`
  if (abs >= 10_000) return `${(value / 1_000).toFixed(1)}k`
  if (abs >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return value.toLocaleString(undefined, { maximumSignificantDigits: 3 })
}

export const fullNumber = (value: number | null | undefined): string =>
  value === null || value === undefined || Number.isNaN(value)
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: 4 })

export const percent = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? '—' : `${value.toFixed(digits)}%`

export const bytes = (value: number): string => {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

export const shortDate = (value: string | null | undefined): string =>
  !value ? '—' : value.slice(0, 10)

export const titleCase = (value: string): string =>
  value.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())

export const cellText = (value: unknown): string => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return fullNumber(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  const text = String(value)
  return text.length > 120 ? `${text.slice(0, 117)}…` : text
}

export const isTimestampLike = (value: unknown): boolean =>
  typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)

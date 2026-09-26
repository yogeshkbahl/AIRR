import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material'
import { EvidenceTag, KeyFigure, ResultTable, SeverityChip } from '../components/Bits'
import { theme } from '../theme'
import { bytes, compactNumber, percent } from '../utils/format'

const wrap = (ui: React.ReactNode) => render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>)

describe('evidence and severity labelling', () => {
  it('labels each evidence class so a fact is never confused with a suggestion', () => {
    wrap(
      <>
        <EvidenceTag kind="fact" />
        <EvidenceTag kind="suggestion" />
        <EvidenceTag kind="assumption" />
      </>,
    )
    expect(screen.getByText('Fact')).toBeInTheDocument()
    expect(screen.getByText('Suggestion')).toBeInTheDocument()
    expect(screen.getByText('Assumption')).toBeInTheDocument()
  })

  it('does not rely on colour alone for severity', () => {
    wrap(<SeverityChip severity="high" />)
    expect(screen.getByText('High')).toBeInTheDocument()
  })
})

describe('result table', () => {
  it('renders headers, values and an em dash for nulls', () => {
    wrap(<ResultTable columns={['region', 'Revenue']} rows={[{ region: 'North', Revenue: null }]} />)
    expect(screen.getByText('region')).toBeInTheDocument()
    expect(screen.getByText('North')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('explains itself when there is nothing to show', () => {
    wrap(<ResultTable columns={[]} rows={[]} />)
    expect(screen.getByText('No rows to show.')).toBeInTheDocument()
  })
})

describe('key figure', () => {
  it('shows the label and the value', () => {
    wrap(<KeyFigure label="Records" value="4,045" />)
    expect(screen.getByText('Records')).toBeInTheDocument()
    expect(screen.getByText('4,045')).toBeInTheDocument()
  })
})

describe('formatting', () => {
  it('keeps large figures readable without losing the unit', () => {
    expect(compactNumber(2_660_495)).toBe('2.66m')
    expect(compactNumber(12_500)).toBe('12.5k')
    expect(compactNumber(null)).toBe('—')
    expect(percent(1.583, 2)).toBe('1.58%')
    expect(bytes(765_337)).toBe('747.4 KB')
  })
})

import { describe, expect, it } from 'vitest'
import {
  canonicalSelection,
  columnLabels,
  isCurrentResponse,
  selectionFingerprint,
  toggleColumn,
} from '../utils/selection'
import {
  MAX_PREFERRED_COLUMNS,
  buildAskPayload,
  isSubmittable,
  newRequestToken,
} from '../utils/askPayload'
import type { ColumnProfile } from '../api/types'

const KNOWN = ['revenue_amount', 'region', 'order_date', 'margin_amount']

describe('ENH-03 canonical selection', () => {
  it('keeps the user order and collapses duplicates', () => {
    const result = canonicalSelection(['region', 'revenue_amount', 'region'], KNOWN)
    expect(result.ids).toEqual(['region', 'revenue_amount'])
    expect(result.dropped).toEqual([])
  })

  it('reports columns that are not in the dataset instead of losing them', () => {
    const result = canonicalSelection(['revenue_amount', 'cost'], KNOWN)
    expect(result.ids).toEqual(['revenue_amount'])
    expect(result.dropped).toEqual(['cost'])
  })

  it('caps the selection at eight columns and says so', () => {
    const many = Array.from({ length: 10 }, (_, index) => `c${index}`)
    const result = canonicalSelection(many, many)
    expect(result.ids).toHaveLength(8)
    expect(result.truncated).toBe(true)
  })

  it('builds a fingerprint in the same format as the backend', () => {
    // backend: f"{dataset_id}|{'>'.join(column_ids)}"
    expect(selectionFingerprint('ds1', ['revenue_amount', 'region'])).toBe('ds1|revenue_amount>region')
    expect(selectionFingerprint('ds1', [])).toBe('ds1|')
  })

  it('treats a reordered selection as a different selection', () => {
    expect(selectionFingerprint('ds1', ['a', 'b'])).not.toBe(selectionFingerprint('ds1', ['b', 'a']))
  })

  it('toggles a column on and off without disturbing the rest', () => {
    expect(toggleColumn(['a', 'b'], 'c')).toEqual(['a', 'b', 'c'])
    expect(toggleColumn(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
  })

  it('resolves display labels from stable ids', () => {
    const columns = [
      { name: 'revenue_amount', label: 'Revenue amount' },
      { name: 'region', label: 'Region' },
    ] as ColumnProfile[]
    expect(columnLabels(['region', 'revenue_amount'], columns)).toEqual(['Region', 'Revenue amount'])
    expect(columnLabels(['unknown'], columns)).toEqual(['unknown'])
  })
})

describe('ENH-03 stale response rejection', () => {
  const fingerprint = selectionFingerprint('ds1', ['revenue_amount', 'region'])

  it('accepts a response for the current dataset and selection', () => {
    expect(
      isCurrentResponse({ dataset_id: 'ds1', selection_fingerprint: fingerprint }, 'ds1', fingerprint),
    ).toBe(true)
  })

  it('rejects a response for a superseded selection', () => {
    const older = selectionFingerprint('ds1', ['revenue_amount'])
    expect(isCurrentResponse({ dataset_id: 'ds1', selection_fingerprint: older }, 'ds1', fingerprint)).toBe(
      false,
    )
  })

  it('rejects a response that belongs to another dataset', () => {
    expect(
      isCurrentResponse({ dataset_id: 'ds2', selection_fingerprint: fingerprint }, 'ds1', fingerprint),
    ).toBe(false)
  })

  it('rejects nothing at all', () => {
    expect(isCurrentResponse(undefined, 'ds1', fingerprint)).toBe(false)
  })
})

describe('ENH-04 ask payload contract', () => {
  it('matches the backend QuestionRequest shape', () => {
    const payload = buildAskPayload({
      question: '  What is total revenue by region?  ',
      columns: ['revenue_amount'],
      quickAskId: 'qa-top_n-1234abcd',
      clientRequestId: 'tok-1',
    })
    expect(payload).toEqual({
      question: 'What is total revenue by region?',
      columns: ['revenue_amount'],
      use_llm: true,
      quick_ask_id: 'qa-top_n-1234abcd',
      client_request_id: 'tok-1',
    })
  })

  it('sends a null quick ask id for a free-text question', () => {
    expect(buildAskPayload({ question: 'How is margin trending?' }).quick_ask_id).toBeNull()
  })

  it('caps preferred columns at the backend limit', () => {
    const payload = buildAskPayload({
      question: 'anything',
      columns: Array.from({ length: 12 }, (_, i) => `c${i}`),
    })
    expect(payload.columns).toHaveLength(MAX_PREFERRED_COLUMNS)
  })

  it('refuses a question shorter than the backend minimum', () => {
    expect(isSubmittable('a')).toBe(false)
    expect(isSubmittable('  ab  ')).toBe(false)
    expect(isSubmittable('abc')).toBe(true)
  })

  it('creates a distinct token per submission so a late answer can be matched', () => {
    expect(newRequestToken()).not.toBe(newRequestToken())
  })
})

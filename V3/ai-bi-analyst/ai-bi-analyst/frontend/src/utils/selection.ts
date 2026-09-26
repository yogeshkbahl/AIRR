import type { ColumnProfile } from '../api/types'

/**
 * ENH-03 — one canonical definition of "what is selected".
 *
 * Column identity is the backend column `name`, never the display label and
 * never an array index. The fingerprint is built the same way here and in
 * `backend/app/core/chart_rules.py:build_fingerprint`, so a response can be
 * matched to the selection that produced it without hashing.
 */

export const MAX_SELECTION = 8

export interface CanonicalSelection {
  ids: string[]
  dropped: string[]
  truncated: boolean
}

/** Order preserved, duplicates collapsed, unknown ids reported not silently lost. */
export function canonicalSelection(requested: string[], known: Iterable<string>): CanonicalSelection {
  const valid = new Set(known)
  const ids: string[] = []
  const dropped: string[] = []
  for (const id of requested) {
    if (!valid.has(id)) {
      dropped.push(id)
      continue
    }
    if (!ids.includes(id)) ids.push(id)
  }
  return { ids: ids.slice(0, MAX_SELECTION), dropped, truncated: ids.length > MAX_SELECTION }
}

export function selectionFingerprint(datasetId: string, ids: string[]): string {
  return `${datasetId}|${ids.join('>')}`
}

export function toggleColumn(current: string[], id: string): string[] {
  return current.includes(id) ? current.filter((value) => value !== id) : [...current, id]
}

export function columnLabels(ids: string[], columns: ColumnProfile[]): string[] {
  const byName = new Map(columns.map((column) => [column.name, column.label]))
  return ids.map((id) => byName.get(id) ?? id)
}

/**
 * True when a response belongs to the selection currently on screen. Used to
 * discard a superseded answer that arrives after a newer one.
 */
export function isCurrentResponse(
  response: { dataset_id: string; selection_fingerprint: string } | undefined,
  datasetId: string,
  fingerprint: string,
): boolean {
  if (!response) return false
  return response.dataset_id === datasetId && response.selection_fingerprint === fingerprint
}

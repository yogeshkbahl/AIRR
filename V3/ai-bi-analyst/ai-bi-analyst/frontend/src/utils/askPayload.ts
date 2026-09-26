/**
 * ENH-04 — the single place the Quick Ask / free-text payload is built.
 *
 * The shape mirrors `QuestionRequest` in `backend/app/schemas.py`; keeping it in
 * one typed function is what makes the frontend-to-backend contract testable
 * rather than scattered through a component.
 */
export interface AskPayload {
  question: string
  columns: string[]
  use_llm: boolean
  quick_ask_id?: string | null
  client_request_id?: string
}

export const MIN_QUESTION_LENGTH = 3
export const MAX_QUESTION_LENGTH = 1000
export const MAX_PREFERRED_COLUMNS = 8

export function buildAskPayload(input: {
  question: string
  columns?: string[]
  quickAskId?: string | null
  clientRequestId?: string
  useLlm?: boolean
}): AskPayload {
  const question = input.question.trim().slice(0, MAX_QUESTION_LENGTH)
  return {
    question,
    columns: (input.columns ?? []).slice(0, MAX_PREFERRED_COLUMNS),
    use_llm: input.useLlm ?? true,
    quick_ask_id: input.quickAskId ?? null,
    client_request_id: input.clientRequestId,
  }
}

export function isSubmittable(question: string): boolean {
  return question.trim().length >= MIN_QUESTION_LENGTH
}

export function newRequestToken(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}

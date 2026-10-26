export interface Spend {
  id: number
  amount: number
  comment: string
  date: string
}

const API_BASE = '/api'

function getToken() {
  return localStorage.getItem('savely-auth') || ''
}

export function setToken(token: string) {
  localStorage.setItem('savely-auth', token)
}

export function clearToken() {
  localStorage.removeItem('savely-auth')
}

export function hasToken() {
  return Boolean(getToken())
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) {
    headers['X-Auth-String'] = token
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (resp.status === 401) {
    clearToken()
    throw new UnauthorizedError()
  }

  if (resp.status === 204) {
    return undefined as T
  }

  if (!resp.ok) {
    let message = `Request failed (${resp.status})`
    try {
      const body = await resp.json()
      if (body && body.error) {
        message = body.error
      }
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }

  return resp.json() as Promise<T>
}

export class UnauthorizedError extends Error {
  constructor() {
    super('unauthorized')
    this.name = 'UnauthorizedError'
  }
}

export function verifyAuth() {
  return request<{ ok: boolean }>('/auth/verify')
}

export function listSpends() {
  return request<Spend[]>('/spends')
}

export function createSpend(payload: { amount: number; comment: string; date?: string }) {
  return request<Spend>('/spends', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateSpend(id: number, payload: { amount: number; comment: string; date?: string }) {
  return request<Spend>(`/spends/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
}

export function deleteSpend(id: number) {
  return request<void>(`/spends/${id}`, { method: 'DELETE' })
}

export interface CategorySpend {
  id: number
  date: string
  amount: number
  comment: string
  confidence: number | null
  cosine: number | null
  source: 'knn' | 'manual' | 'fallback' | 'none' | string
  needsReview: boolean
}

export interface CategoryStat {
  name: string
  total: number
  count: number
  share: number
  avg: number
  spends: CategorySpend[]
}

export interface StatsMetrics {
  rows: number
  uniqueComments: number
  classified: number
  unclassified: number
  lowConfidence: number
  latencyMs: number
  degraded: boolean
}

export interface MonthStats {
  month: string
  total: number
  byCategory: CategoryStat[]
  summary: string
  categories: string[]
  metrics: StatsMetrics
  classifier: { enabled: boolean; cosineFloor: number; reviewCosine: number }
}

export function getStats(month?: string, refresh = false) {
  const params = new URLSearchParams()
  if (month) params.set('month', month)
  if (refresh) params.set('refresh', '1')
  const query = params.toString()
  return request<MonthStats>(`/stats${query ? `?${query}` : ''}`)
}

export function setSpendCategory(id: number, category: string) {
  return request<{ id: number; category: string; source: string }>(
    `/spends/${id}/category`,
    { method: 'PATCH', body: JSON.stringify({ category }) },
  )
}

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

export interface AiCategory {
  name: string
  spends: { date: string; amount: number; comment: string }[]
}

export interface AiAnalysis {
  categories: AiCategory[]
  notice: string
  month: string
}

export function analyzeSpends() {
  return request<AiAnalysis>('/ai/analyze', { method: 'POST' })
}

export interface AiCost {
  totalCostRub: number
  todayCostRub: number
  dailyLimitRub: number
}

export function getAiCost(since?: number) {
  const query = since !== undefined ? `?since=${since}` : ''
  return request<AiCost>(`/ai/cost${query}`)
}

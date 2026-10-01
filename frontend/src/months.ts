import type { Spend } from './api'

export interface MonthGroup {
  key: string
  label: string
  total: number
  spends: Spend[]
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}

// Spend dates arrive as naive ISO strings ("2026-10-01T12:00:00"), which the
// browser parses in local time — so grouping follows the user's local calendar.
export function monthKeyOf(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) {
    return typeof date === 'string' ? date.slice(0, 7) : ''
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

export function currentMonthKey(): string {
  return monthKeyOf(new Date())
}

export function monthLabel(key: string): string {
  const [year, month] = key.split('-')
  const d = new Date(Number(year), Number(month) - 1, 1)
  if (isNaN(d.getTime())) return key
  return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}

export function groupByMonth(spends: Spend[]): MonthGroup[] {
  const groups = new Map<string, Spend[]>()
  for (const s of spends) {
    const key = monthKeyOf(s.date)
    const bucket = groups.get(key)
    if (bucket) {
      bucket.push(s)
    } else {
      groups.set(key, [s])
    }
  }
  return [...groups.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([key, monthSpends]) => ({
      key,
      label: monthLabel(key),
      total: monthSpends.reduce((sum, s) => sum + s.amount, 0),
      spends: monthSpends,
    }))
}

// Local wall time without a UTC offset, matching the format used for manual
// dates. Keeps quick-added spends in the user's month instead of UTC's.
export function toLocalIso(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}
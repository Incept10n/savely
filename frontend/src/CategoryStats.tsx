import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getStats,
  setSpendCategory,
  type MonthStats,
  type Spend,
} from './api'
import { currentMonthKey, groupByMonth, monthLabel } from './months'

interface Props {
  spends: Spend[]
}

function sourceNote(source: string) {
  if (source === 'manual') return 'confirmed by you'
  if (source === 'fallback') return 'empty comment'
  if (source === 'none') return 'not classified'
  return ''
}

export default function CategoryStats({ spends }: Props) {
  const [month, setMonth] = useState(() => currentMonthKey())
  const [stats, setStats] = useState<MonthStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [fixing, setFixing] = useState<number | null>(null)

  const months = useMemo(() => {
    const keys = groupByMonth(spends).map((g) => g.key)
    if (!keys.includes(month)) keys.unshift(month)
    return keys
  }, [spends, month])

  const load = useCallback(
    async (target: string, refresh = false) => {
      setLoading(true)
      setError('')
      try {
        setStats(await getStats(target, refresh))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load categories')
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    load(month)
  }, [month, spends, load])

  async function correct(id: number, category: string) {
    setFixing(id)
    try {
      await setSpendCategory(id, category)
      await load(month)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save category')
    } finally {
      setFixing(null)
    }
  }

  const categories = stats?.categories ?? []
  const visible = stats?.byCategory ?? []
  const maxTotal = visible.reduce((max, c) => Math.max(max, c.total), 0)
  const degraded = Boolean(stats?.metrics.degraded)

  return (
    <div className="card stats-card">
      <div className="stats-head">
        <select
          className="stats-month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
        >
          {months.map((key) => (
            <option key={key} value={key}>
              {monthLabel(key)}
            </option>
          ))}
        </select>
        <button
          className="btn btn-ai"
          onClick={() => load(month, true)}
          disabled={loading}
          title="Re-run the classifier for every spend that was not confirmed by hand"
        >
          {loading ? 'Analyzing…' : 'Analyze with AI'}
        </button>
      </div>

      {error && <div className="error ai-error">{error}</div>}

      {degraded && (
        <div className="stats-warn">
          Classifier is unavailable — showing the categories already saved. New
          spends stay uncategorised until it is back.
        </div>
      )}

      {stats && (
        <>
          <div className="stats-summary">{stats.summary}</div>

          {visible.length === 0 && (
            <div className="empty-note">No spends in this month yet.</div>
          )}

          {visible.map((cat) => (
            <div className="cat-row" key={cat.name}>
              <div className="cat-head">
                <span className="cat-name">{cat.name}</span>
                <span className="cat-amount">{cat.total.toFixed(2)} ₽</span>
              </div>
              <div className="cat-track">
                <div
                  className="cat-fill"
                  style={{
                    width: `${maxTotal ? Math.max((cat.total / maxTotal) * 100, 2) : 0}%`,
                  }}
                />
              </div>
              <div className="cat-meta">
                {cat.count} шт · {cat.share.toFixed(1)}% · средний {cat.avg.toFixed(2)} ₽
              </div>

              <details className="cat-spends">
                <summary>
                  {cat.spends.some((s) => s.needsReview)
                    ? `${cat.count} spends · есть неподтверждённые`
                    : `${cat.count} spends`}
                </summary>
                <ul>
                  {cat.spends.map((spend) => (
                    <li
                      key={spend.id}
                      className={spend.needsReview ? 'cat-spend review' : 'cat-spend'}
                    >
                      <span className="cat-spend-main">
                        <span className="cat-spend-date">
                          {new Date(spend.date).toLocaleDateString()}
                        </span>
                        <span className="cat-spend-comment">
                          {spend.comment || '—'}
                        </span>
                        {spend.needsReview && (
                          <span className="badge">confirm</span>
                        )}
                        {sourceNote(spend.source) && (
                          <span className="cat-spend-source">
                            {sourceNote(spend.source)}
                          </span>
                        )}
                      </span>
                      <span className="cat-spend-right">
                        <span className="cat-spend-amount">
                          {spend.amount.toFixed(2)} ₽
                        </span>
                        <select
                          className="cat-fix"
                          value={spend.needsReview ? '' : cat.name}
                          disabled={fixing === spend.id}
                          onChange={(e) => {
                            if (e.target.value) correct(spend.id, e.target.value)
                          }}
                          title="Fix the category by hand"
                        >
                          <option value="">fix…</option>
                          {categories.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          ))}

          <div className="ai-metrics">
            {stats.metrics.rows} spends · {stats.metrics.uniqueComments} unique
            comments · classified {stats.metrics.classified} ·{' '}
            {stats.metrics.unclassified} unclassified ·{' '}
            {stats.metrics.lowConfidence} to confirm · {stats.metrics.latencyMs} ms
          </div>
        </>
      )}
    </div>
  )
}

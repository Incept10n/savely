import { useEffect, useState } from 'react'
import { analyzeSpends, getAiCost, type AiAnalysis, type Spend } from './api'
import { currentMonthKey, monthKeyOf, monthLabel } from './months'

interface Props {
  spends: Spend[]
  onAdd: (amount: number, comment: string) => Promise<void>
}

export default function SpendsTab({ spends, onAdd }: Props) {
  const [amount, setAmount] = useState('')
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const [aiLoading, setAiLoading] = useState(false)
  const [analysis, setAnalysis] = useState<AiAnalysis | null>(null)
  const [aiError, setAiError] = useState('')
  const [aiTotalCost, setAiTotalCost] = useState(0)
  const [aiTodayCost, setAiTodayCost] = useState(0)
  const [aiDailyLimit, setAiDailyLimit] = useState(15)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    const num = parseFloat(amount)
    if (isNaN(num) || num < 0) {
      setError('Enter a valid amount')
      return
    }
    setLoading(true)
    try {
      await onAdd(num, comment.trim())
      setAmount('')
      setComment('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setLoading(false)
    }
  }

  async function loadAiCost() {
    const localMidnight = new Date()
    localMidnight.setHours(0, 0, 0, 0)
    try {
      const c = await getAiCost(localMidnight.getTime())
      setAiTotalCost(c.totalCostRub)
      setAiTodayCost(c.todayCostRub)
      setAiDailyLimit(c.dailyLimitRub)
    } catch {
      /* non-fatal */
    }
  }

  useEffect(() => {
    loadAiCost()
  }, [])

  async function handleAnalyze() {
    setAiLoading(true)
    setAiError('')
    setAnalysis(null)
    try {
      const result = await analyzeSpends()
      setAnalysis(result)
      loadAiCost()
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'AI analysis failed')
    } finally {
      setAiLoading(false)
    }
  }

  const monthKey = currentMonthKey()
  const monthSpends = spends.filter((s) => monthKeyOf(s.date) === monthKey)
  const total = monthSpends.reduce((sum, s) => sum + s.amount, 0)
  const uniqueDays = new Set(monthSpends.map((s) => s.date.slice(0, 10))).size
  const dailyAvg = uniqueDays > 0 ? total / uniqueDays : 0

  const aiBlocked = aiTodayCost > aiDailyLimit

  return (
    <div className="summary-card">
      <div className="summary-month">{monthLabel(monthKey)}</div>
      <div className="summary-total">{total.toFixed(2)} ₽</div>
      {uniqueDays > 0 && (
        <div className="summary-avg">≈ {dailyAvg.toFixed(2)} ₽ / day</div>
      )}

      <button
        className="btn btn-ai"
        onClick={handleAnalyze}
        disabled={aiLoading || monthSpends.length === 0 || aiBlocked}
        title={aiBlocked ? 'AI is disabled for today (daily cost limit reached)' : undefined}
      >
        {aiLoading ? 'Analyzing…' : 'Analyze with AI'}
      </button>
      {aiBlocked && (
        <div className="ai-blocked">
          Analyze with AI is disabled for today — you already used {aiTodayCost.toFixed(2)} ₽
          of AI today, over the {aiDailyLimit.toFixed(0)} ₽ daily limit.
        </div>
      )}
      {aiError && <div className="error ai-error">{aiError}</div>}

      {analysis && (
        <div className="ai-result">
          <div className="ai-notice">{analysis.notice}</div>
          <div className="ai-month">Analysis covers {analysis.month} only</div>
          {analysis.categories.map((cat) => (
            <div className="ai-category" key={cat.name}>
              <div className="ai-category-head">
                <span className="ai-category-name">{cat.name}</span>
                <span className="ai-category-stats">
                  {cat.count} · {cat.total.toFixed(2)} ₽ · {cat.share.toFixed(1)}%
                </span>
              </div>
              <ul className="ai-spends">
                {cat.spends.map((s, i) => (
                  <li key={i} className="ai-spend">
                    <span>
                      {new Date(s.date).toLocaleDateString()} · {s.comment || '—'}
                    </span>
                    <span className="ai-amount">{s.amount.toFixed(2)} ₽</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <div className="ai-metrics">
            {analysis.metrics.rows} spends · {analysis.metrics.uniqueComments} unique comments ·{' '}
            {analysis.metrics.estimatedInputTokens}+{analysis.metrics.maxOutputTokens} tokens ·
            est {analysis.metrics.estimatedCost.toFixed(2)} ₽ · actual{' '}
            {analysis.metrics.actualCost.toFixed(2)} ₽
          </div>
        </div>
      )}

      <div className="ai-cost">
        AI total spent: {aiTotalCost.toFixed(2)} ₽ · today: {aiTodayCost.toFixed(2)} ₽ /{' '}
        {aiDailyLimit.toFixed(0)} ₽
      </div>

      <form className="card" onSubmit={handleSubmit}>
        <div className="field">
          <label>Amount, ₽</label>
          <input
            type="number"
            step="0.01"
            min="0"
            placeholder="0.00 ₽"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label>Comment</label>
          <input
            type="text"
            placeholder="What did you spend on?"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
        {error && <div className="error">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={loading}>
          {loading ? 'Saving…' : 'Apply'}
        </button>
      </form>
    </div>
  )
}

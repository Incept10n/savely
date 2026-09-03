import { useEffect, useState } from 'react'
import { analyzeSpends, getAiCost, type AiAnalysis, type Spend } from './api'

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
  const [aiCost, setAiCost] = useState(0)

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
    try {
      const c = await getAiCost()
      setAiCost(c.totalCostRub)
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

  const total = spends.reduce((sum, s) => sum + s.amount, 0)
  const uniqueDays = new Set(spends.map((s) => s.date.slice(0, 10))).size
  const dailyAvg = uniqueDays > 0 ? total / uniqueDays : 0

  return (
    <div className="summary-card">
      <div className="summary-total">{total.toFixed(2)} ₽</div>
      {uniqueDays > 0 && (
        <div className="summary-avg">≈ {dailyAvg.toFixed(2)} ₽ / day</div>
      )}

      <button
        className="btn btn-ai"
        onClick={handleAnalyze}
        disabled={aiLoading || spends.length === 0}
      >
        {aiLoading ? 'Analyzing…' : 'Analyze with AI'}
      </button>
      {aiError && <div className="error ai-error">{aiError}</div>}

      {analysis && (
        <div className="ai-result">
          <div className="ai-notice">{analysis.notice}</div>
          <div className="ai-month">Analysis covers {analysis.month} only</div>
          {analysis.categories.map((cat) => (
            <div className="ai-category" key={cat.name}>
              <div className="ai-category-name">{cat.name}</div>
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
        </div>
      )}

      <div className="ai-cost">AI total spent: {aiCost.toFixed(2)} ₽</div>

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

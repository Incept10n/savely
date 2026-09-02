import { useState } from 'react'

interface Props {
  onAdd: (amount: number, comment: string) => Promise<void>
}

export default function SpendsTab({ onAdd }: Props) {
  const [amount, setAmount] = useState('')
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

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

  return (
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
  )
}

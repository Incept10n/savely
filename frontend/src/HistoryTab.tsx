import { useState } from 'react'
import type { Spend } from './api'
import { currentMonthKey, groupByMonth, monthKeyOf, monthLabel } from './months'

interface Props {
  spends: Spend[]
  onAdd: (amount: number, comment: string, date?: string) => Promise<void>
  onUpdate: (id: number, amount: number, comment: string, date?: string) => Promise<void>
  onDelete: (id: number) => Promise<void>
}

function toLocalInput(dateIso: string) {
  const d = new Date(dateIso)
  if (isNaN(d.getTime())) {
    return dateIso.slice(0, 10)
  }
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export default function HistoryTab({ spends, onAdd, onUpdate, onDelete }: Props) {
  const [editing, setEditing] = useState<number | null>(null)
  const [newAmount, setNewAmount] = useState('')
  const [newComment, setNewComment] = useState('')
  const [newDate, setNewDate] = useState('')
  const [editAmount, setEditAmount] = useState('')
  const [editComment, setEditComment] = useState('')
  const [editDate, setEditDate] = useState('')
  const [error, setError] = useState('')

  const monthKey = currentMonthKey()
  const monthSpends = spends.filter((s) => monthKeyOf(s.date) === monthKey)
  const months = groupByMonth(spends)

  function startAdd() {
    setEditing(-1)
    setError('')
    setNewAmount('')
    setNewComment('')
    setNewDate('')
  }

  function startEdit(s: Spend) {
    setEditing(s.id)
    setError('')
    setEditAmount(String(s.amount))
    setEditComment(s.comment)
    setEditDate(toLocalInput(s.date))
  }

  async function submitAdd(e: React.FormEvent) {
    e.preventDefault()
    const num = parseFloat(newAmount)
    if (isNaN(num) || num < 0) {
      setError('Enter a valid amount')
      return
    }
    try {
      await onAdd(num, newComment.trim(), newDate || undefined)
      setEditing(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    }
  }

  async function submitEdit(e: React.FormEvent) {
    e.preventDefault()
    if (editing === null) return
    const num = parseFloat(editAmount)
    if (isNaN(num) || num < 0) {
      setError('Enter a valid amount')
      return
    }
    try {
      await onUpdate(editing, num, editComment.trim(), editDate || undefined)
      setEditing(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm('Delete this spend?')) return
    try {
      await onDelete(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete')
    }
  }

  return (
    <div>
      <div className="card">
        <button className="btn btn-ghost" onClick={startAdd}>
          + Add with date
        </button>

        {editing === -1 && (
          <form style={{ marginTop: '12px' }} onSubmit={submitAdd}>
            <div className="row">
              <div className="field grow">
                <label>Amount, ₽</label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={newAmount}
                  onChange={(e) => setNewAmount(e.target.value)}
                  required
                />
              </div>
              <div className="field grow">
                <label>Date</label>
                <input
                  type="date"
                  value={newDate}
                  onChange={(e) => setNewDate(e.target.value)}
                />
              </div>
            </div>
            <div className="field">
              <label>Comment</label>
              <input
                type="text"
                value={newComment}
                onChange={(e) => setNewComment(e.target.value)}
              />
            </div>
            {error && <div className="error">{error}</div>}
            <div className="row">
              <button className="btn btn-primary" type="submit">
                Save
              </button>
              <button className="btn btn-ghost" type="button" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card">
        <div className="card-title">
          {monthLabel(monthKey)} · {monthSpends.reduce((sum, s) => sum + s.amount, 0).toFixed(2)} ₽
        </div>
        <ul className="spend-list">
          {monthSpends.length === 0 && (
            <li className="empty-note">No spends yet.</li>
          )}
          {monthSpends.map((s) =>
            editing === s.id ? (
              <li key={s.id}>
                <form onSubmit={submitEdit}>
                  <div className="row">
                    <div className="field grow">
                      <label>Amount, ₽</label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={editAmount}
                        onChange={(e) => setEditAmount(e.target.value)}
                        required
                      />
                    </div>
                    <div className="field grow">
                      <label>Date</label>
                      <input
                        type="date"
                        value={editDate}
                        onChange={(e) => setEditDate(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="field">
                    <label>Comment</label>
                    <input
                      type="text"
                      value={editComment}
                      onChange={(e) => setEditComment(e.target.value)}
                    />
                  </div>
                  {error && <div className="error">{error}</div>}
                  <div className="row">
                    <button className="btn btn-primary" type="submit">
                      Save
                    </button>
                    <button
                      className="btn btn-ghost"
                      type="button"
                      onClick={() => setEditing(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              </li>
            ) : (
              <li key={s.id} className="spend-item">
                <div>
                  <div className="spend-amount">{s.amount.toFixed(2)} ₽</div>
                  {s.comment && <div className="spend-comment">{s.comment}</div>}
                  <div className="spend-date">{new Date(s.date).toLocaleString()}</div>
                </div>
                <div className="spend-actions">
                  <button className="btn btn-ghost" onClick={() => startEdit(s)}>
                    Edit
                  </button>
                  <button className="btn btn-danger" onClick={() => handleDelete(s.id)}>
                    Delete
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      </div>

      {months.length > 0 && (
        <div className="card">
          <div className="card-title">By month</div>
          {months.map((month) => (
            <details className="month-block" key={month.key}>
              <summary className="month-summary">
                <span className="month-summary-row">
                  <span>{month.label}</span>
                  <span className="month-total">{month.total.toFixed(2)} ₽</span>
                </span>
              </summary>
              <ul className="month-spends">
                {month.spends.map((s) => (
                  <li key={s.id} className="month-spend">
                    <span className="month-spend-label">
                      {toLocalInput(s.date)} · {s.comment || '—'}
                    </span>
                    <span className="month-spend-amount">{s.amount.toFixed(2)} ₽</span>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

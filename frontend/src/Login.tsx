import { useState } from 'react'
import { setToken, verifyAuth, UnauthorizedError } from './api'

interface Props {
  onSuccess: () => void
}

export default function Login({ onSuccess }: Props) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      setToken(value.trim())
      await verifyAuth()
      onSuccess()
    } catch (err) {
      setToken('')
      if (err instanceof UnauthorizedError) {
        setError('Wrong access string')
      } else {
        setError(err instanceof Error ? err.message : 'Login failed')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="login" onSubmit={handleSubmit}>
      <h1>Savely</h1>
      <p>Enter access string to continue</p>
      <div className="field">
        <input
          type="password"
          placeholder="Access string"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
      </div>
      {error && <div className="error">{error}</div>}
      <button className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
        {loading ? 'Checking…' : 'Login'}
      </button>
    </form>
  )
}

import { useEffect, useState } from 'react'
import Login from './Login.tsx'
import SpendsTab from './SpendsTab.tsx'
import HistoryTab from './HistoryTab.tsx'
import {
  clearToken,
  hasToken,
  verifyAuth,
  listSpends,
  createSpend,
  updateSpend,
  deleteSpend,
  UnauthorizedError,
  type Spend,
} from './api.ts'

type Tab = 'spends' | 'history'

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(hasToken())
  const [tab, setTab] = useState<Tab>('spends')
  const [spends, setSpends] = useState<Spend[]>([])
  const [error, setError] = useState('')

  function load() {
    listSpends()
      .then(setSpends)
      .catch((err) => {
        if (err instanceof UnauthorizedError) {
          setAuthenticated(false)
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load')
        }
      })
  }

  useEffect(() => {
    if (!hasToken()) {
      setAuthenticated(false)
      return
    }
    verifyAuth()
      .then(() => {
        setAuthenticated(true)
        load()
      })
      .catch((err) => {
        if (err instanceof UnauthorizedError) {
          setAuthenticated(false)
        } else {
          setError(err instanceof Error ? err.message : 'Login check failed')
        }
      })
  }, [])

  if (!authenticated) {
    return <Login onSuccess={() => setAuthenticated(true)} />
  }

  async function addSpend(amount: number, comment: string, date?: string) {
    await createSpend({ amount, comment, ...(date ? { date: `${date}T12:00:00` } : {}) })
    load()
  }

  async function editSpend(id: number, amount: number, comment: string, date?: string) {
    await updateSpend(id, { amount, comment, ...(date ? { date: `${date}T12:00:00` } : {}) })
    load()
  }

  async function removeSpend(id: number) {
    await deleteSpend(id)
    load()
  }

  function logout() {
    clearToken()
    setAuthenticated(false)
  }

  return (
    <div className="app">
      <div className="header">
        <h1>Savely</h1>
        <button className="logout" onClick={logout}>
          Logout
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="tabs">
        <button className={tab === 'spends' ? 'active' : ''} onClick={() => setTab('spends')}>
          Spends
        </button>
        <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
          History
        </button>
      </div>
      {tab === 'spends' ? (
        <SpendsTab onAdd={addSpend} />
      ) : (
        <HistoryTab
          spends={spends}
          onAdd={addSpend}
          onUpdate={editSpend}
          onDelete={removeSpend}
        />
      )}
    </div>
  )
}

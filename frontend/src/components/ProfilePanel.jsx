import React, { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../auth/apiFetch'

/* ---- Telegram linking section ---- */
function TelegramLinking() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [linkInfo, setLinkInfo] = useState(null)
  const [error, setError] = useState(null)
  const pollIntervalRef = useRef(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch('/api/profile/telegram')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setStatus(data)
      return data
    } catch (err) {
      setError(String(err))
      return null
    }
  }, [])

  useEffect(() => {
    fetchStatus().finally(() => setLoading(false))
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }
  }, [fetchStatus])

  // When the user has requested a token, poll the status until linked.
  useEffect(() => {
    if (!linkInfo) return
    if (status?.linked) {
      // Already linked → stop polling and clear the deep-link.
      setLinkInfo(null)
      return
    }
    pollIntervalRef.current = setInterval(async () => {
      const data = await fetchStatus()
      if (data?.linked) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
        setLinkInfo(null)
      }
    }, 3000)
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [linkInfo, status?.linked, fetchStatus])

  const handleGenerate = async () => {
    setError(null)
    try {
      const res = await apiFetch('/api/profile/telegram/link-token', { method: 'POST' })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setLinkInfo(data)
    } catch (err) {
      setError(String(err))
    }
  }

  const handleUnlink = async () => {
    if (!confirm('¿Desvincular Telegram? Dejarás de recibir alertas hasta que vuelvas a vincular.')) return
    setError(null)
    try {
      const res = await apiFetch('/api/profile/telegram', { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchStatus()
    } catch (err) {
      setError(String(err))
    }
  }

  if (loading) {
    return <div style={{ color: 'var(--text-muted)' }}>Cargando…</div>
  }

  if (!status?.bot_configured) {
    return (
      <div style={{
        padding: 'var(--space-3)', border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)', fontSize: '0.9rem',
      }}>
        El bot de Telegram no está configurado en este servidor. Pide al administrador que defina
        <code style={{ margin: '0 4px' }}>TELEGRAM_BOT_TOKEN</code> y
        <code style={{ margin: '0 4px' }}>TELEGRAM_BOT_USERNAME</code>.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      {status.linked ? (
        <div style={{
          padding: 'var(--space-3)', background: 'rgba(34,197,94,0.08)',
          border: '1px solid rgba(34,197,94,0.3)', borderRadius: 'var(--radius-sm)',
          display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
        }}>
          <span style={{ fontSize: '1.2rem' }}>✅</span>
          <div style={{ flex: 1 }}>
            <div style={{ color: 'var(--color-success)', fontWeight: 600 }}>Vinculado</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
              {status.telegram_username ? `@${status.telegram_username}` : 'chat vinculado'}
              {status.linked_at ? ` · ${new Date(status.linked_at).toLocaleString()}` : ''}
            </div>
          </div>
          <button className="btn btn-sm btn-secondary" onClick={handleUnlink} style={{ color: 'var(--color-danger)' }}>
            Desvincular
          </button>
        </div>
      ) : (
        <div>
          <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
            Vincula tu cuenta con Telegram para recibir alertas de entradas, salidas y stops en tus
            configuraciones que lo tengan activado.
          </p>
          {!linkInfo && (
            <button className="btn btn-primary" onClick={handleGenerate}>
              Vincular Telegram
            </button>
          )}
          {linkInfo && (
            <div style={{
              padding: 'var(--space-3)', background: 'var(--bg-elevated)',
              border: '1px solid var(--border-default)', borderRadius: 'var(--radius-sm)',
              display: 'flex', flexDirection: 'column', gap: 'var(--space-2)',
            }}>
              <div style={{ color: 'var(--text-secondary)' }}>
                1. Abre el enlace y pulsa <strong>START</strong> o envía <code>/start {linkInfo.token}</code>.
              </div>
              <a
                href={linkInfo.deep_link}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary"
                style={{ display: 'inline-block', textDecoration: 'none' }}
              >
                📲 Abrir Telegram &nbsp;→&nbsp; @{linkInfo.bot_username}
              </a>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                2. Cuando completes el paso, esta pantalla se actualizará sola. El código caduca
                a las {new Date(linkInfo.expires_at).toLocaleTimeString()}.
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div style={{
          padding: '8px 12px', background: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.25)', borderRadius: 'var(--radius-sm)',
          color: 'var(--color-danger)', fontSize: '0.83rem',
        }}>{error}</div>
      )}
    </div>
  )
}

export default function ProfilePanel() {
  return (
    <div className="panel-section">
      <div className="card">
        <div className="card-header">
          <span className="card-title">Telegram</span>
        </div>
        <div className="card-body">
          <TelegramLinking />
        </div>
      </div>
    </div>
  )
}

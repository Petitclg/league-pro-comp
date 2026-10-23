import { useEffect, useRef, useState } from 'react'
import { api, type ImportStatus } from './api'

/** Shown while the app re-imports data stored by an older version; reloads once the corrected data is ready. */
export function RepairBanner() {
  const [status, setStatus] = useState<ImportStatus>({ state: 'idle' })
  const sawRepair = useRef(false)
  const running = status.state === 'running'

  useEffect(() => {
    let alive = true
    const tick = () =>
      api.importStatus().then(
        (s) => alive && setStatus(s),
        () => undefined,
      )
    tick()
    if (!running) return () => void (alive = false)
    const id = window.setInterval(tick, 2000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [running])

  useEffect(() => {
    if (running && status.repair) sawRepair.current = true
    if (sawRepair.current && status.state === 'done') {
      const id = window.setTimeout(() => window.location.reload(), 1200)
      return () => window.clearTimeout(id)
    }
  }, [running, status.repair, status.state])

  if (!status.repair) return null
  const pct = status.total ? Math.round(((status.done ?? 0) / status.total) * 100) : 0
  if (running) {
    return (
      <div className="repair-banner" role="status">
        Mise à jour des données en cours ({pct} % · {status.current ?? 'démarrage…'}) : une correction s'applique aux parties déjà
        importées. Les équipes et joueurs de certaines parties seront corrigés à la fin.
        <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div style={{ width: `${pct}%` }} />
        </div>
      </div>
    )
  }
  if (status.state === 'error') {
    return (
      <div className="repair-banner error" role="status">
        La mise à jour des données a échoué ({status.error}). Elle sera retentée au prochain lancement ; vérifiez la connexion à internet.
      </div>
    )
  }
  return null
}

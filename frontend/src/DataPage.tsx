import { useEffect, useRef, useState } from 'react'
import { api, type ImportStatus, type Meta } from './api'

const FIRST_YEAR = 2017

/** Import / update of the data from Leaguepedia, with progress. Also shown alone on a fresh install. */
export function DataPage({ meta }: { meta: Meta }) {
  const [status, setStatus] = useState<ImportStatus>({ state: 'idle' })
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>(['LCK'])
  const [fromYear, setFromYear] = useState(FIRST_YEAR)
  const sawRunning = useRef(false)
  const thisYear = new Date().getFullYear()
  const hasData = meta.games > 0
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
    sawRunning.current = true
    const id = window.setInterval(tick, 1000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [running])

  useEffect(() => {
    // The scope was rebuilt on the server: reload once an import we watched is over.
    if (sawRunning.current && (status.state === 'done' || status.state === 'cancelled')) {
      const id = window.setTimeout(() => window.location.reload(), 1200)
      return () => window.clearTimeout(id)
    }
  }, [status.state])

  const start = (leagues: string[], first_year: number) => {
    setError(null)
    api.startImport({ leagues, first_year }).then(setStatus, (e) => setError(String(e)))
  }
  const toggle = (key: string) => setPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))
  const pctDone = status.total ? Math.round(((status.done ?? 0) / status.total) * 100) : 0
  const allKeys = meta.registry.map((l) => l.key)

  return (
    <section className="data-page">
      {!hasData && (
        <div className="card wide">
          <h2>Bienvenue 👋</h2>
          <p>
            Aucune donnée pour l'instant. Les drafts viennent de <a href="https://lol.fandom.com" target="_blank" rel="noreferrer">Leaguepedia</a>{' '}
            (CC BY-SA) : choisissez ce qu'il faut importer. Le plus rapide pour commencer est la LCK (environ une minute).
          </p>
        </div>
      )}

      <div className="card wide">
        <h2>Importer / mettre à jour les données</h2>
        {running ? (
          <div>
            <div className="progress" role="progressbar" aria-valuenow={pctDone} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${pctDone}%` }} />
            </div>
            <p>
              {pctDone} % · {status.current ?? 'démarrage…'} · {status.games ?? 0} parties importées
              <button className="link-btn" onClick={() => api.cancelImport().then(setStatus)}>
                Annuler
              </button>
            </p>
            {status.log && status.log.length > 0 && <pre className="import-log">{status.log.join('\n')}</pre>}
          </div>
        ) : (
          <>
            <div className="import-presets">
              <button disabled={!hasData} onClick={() => start([], thisYear)} title="Ré-importe l'année en cours pour les ligues déjà présentes">
                Mettre à jour (année en cours)
              </button>
              <button onClick={() => start(['LCK'], FIRST_YEAR)}>LCK, depuis 2017 (≈ 1 min)</button>
              <button onClick={() => start(allKeys, FIRST_YEAR)}>Toutes les ligues, depuis 2017 (≈ 10 à 20 min)</button>
            </div>
            <details>
              <summary>Choisir les ligues et la période</summary>
              <div className="import-grid">
                {meta.registry.map((l) => (
                  <label key={l.key}>
                    <input type="checkbox" checked={picked.includes(l.key)} onChange={() => toggle(l.key)} /> {l.name}
                  </label>
                ))}
              </div>
              <p>
                <label>
                  À partir de{' '}
                  <select value={fromYear} onChange={(e) => setFromYear(Number(e.target.value))}>
                    {Array.from({ length: thisYear - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i).map((y) => (
                      <option key={y} value={y}>
                        {y}
                      </option>
                    ))}
                  </select>
                </label>{' '}
                <button disabled={picked.length === 0} onClick={() => start(picked, fromYear)}>
                  Importer la sélection
                </button>
              </p>
            </details>
            {status.state === 'done' && <p className="good-text">Import terminé : {status.games} parties.</p>}
            {status.state === 'cancelled' && <p className="muted">Import annulé (les parties déjà importées sont conservées).</p>}
            {(status.state === 'error' || error) && (
              <p className="error">Import impossible : {status.error ?? error}. Vérifiez la connexion à internet.</p>
            )}
          </>
        )}
        <p className="muted">
          Un import remplace les parties déjà présentes (pas de doublons) et peut être relancé à tout moment ; l'application reste utilisable
          pendant ce temps.
        </p>
      </div>

      {hasData && (
        <div className="card wide">
          <h2>Données en base</h2>
          <table>
            <thead>
              <tr>
                <th>Ligue</th>
                <th>Région</th>
                <th>Parties</th>
                <th>Période</th>
              </tr>
            </thead>
            <tbody>
              {meta.leagues
                .filter((l) => l.games > 0)
                .map((l) => (
                  <tr key={l.key}>
                    <td>{l.name}</td>
                    <td>{l.region}</td>
                    <td>{l.games}</td>
                    <td>
                      {l.first} – {l.last}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="muted">
        LoL Pro Comp v{meta.version} · données © contributeurs de Leaguepedia (CC BY-SA 3.0) · non affilié à Riot Games.
      </p>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { api, type SoloqImportStatus, type SoloqOverview, type SoloqSettings } from './api'
import { RANK_LABEL } from './scope'

const KEY_STATE: Record<string, { text: string; className: string }> = {
  missing: { text: 'aucune clé', className: 'muted' },
  unchecked: { text: 'non vérifiée', className: 'muted' },
  valid: { text: 'valide', className: 'good-text' },
  invalid: { text: 'expirée ou invalide', className: 'bad-text' },
}

/** Solo queue import through the Riot API: key, sampling settings, progress and stored games. */
export function SoloqData() {
  const [overview, setOverview] = useState<SoloqOverview | null>(null)
  const [settings, setSettings] = useState<SoloqSettings | null>(null)
  const [status, setStatus] = useState<SoloqImportStatus>({ state: 'idle' })
  const [keyInput, setKeyInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const running = status.state === 'running'

  const refresh = () =>
    api.soloq().then((o) => {
      setOverview(o)
      setStatus(o.import)
      setSettings((s) => s ?? o.settings)
    }, () => undefined)

  useEffect(() => {
    refresh()
  }, [])

  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => api.soloqImportStatus().then(setStatus, () => undefined), 2000)
    return () => {
      window.clearInterval(id)
      refresh() // the import stopped: stored games and key state changed
    }
  }, [running])

  const act = (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    action()
      .then(refresh, (e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  if (!overview || !settings) return null
  const key = overview.key
  const stored = overview.stored
  const set = <K extends keyof SoloqSettings>(k: K, v: SoloqSettings[K]) => setSettings({ ...settings, [k]: v })
  const toggle = (k: 'platforms' | 'tiers', v: string) =>
    set(k, settings[k].includes(v) ? settings[k].filter((x) => x !== v) : [...settings[k], v])
  const target = status.target ?? settings.target
  const games = status.games ?? stored.games
  const pctDone = target ? Math.min(100, Math.round((games / target) * 100)) : 0
  const canStart = key.state !== 'missing' && key.state !== 'invalid' && settings.platforms.length > 0 && settings.tiers.length > 0

  return (
    <div className="card wide">
      <h2>Solo queue (API Riot)</h2>
      <p className="muted">
        Parties classées solo/duo de rang Émeraude et plus, importées avec l'API Riot dans une base séparée (<code>soloq.db</code>). Il faut une clé
        personnelle (<a href="https://developer.riotgames.com" target="_blank" rel="noreferrer">developer.riotgames.com</a>) ; une clé de développement
        expire au bout de 24 h. La clé reste sur cet ordinateur et n'est jamais affichée en clair.
      </p>

      <h3>Clé API</h3>
      <p>
        {key.masked && <code>{key.masked}</code>} <span className={KEY_STATE[key.state].className}>{KEY_STATE[key.state].text}</span>
        {key.state !== 'missing' && (
          <>
            {' '}
            <button className="link-btn" disabled={busy} onClick={() => act(api.soloqCheckKey)}>
              Vérifier
            </button>
            <button className="link-btn" disabled={busy || running} onClick={() => act(api.soloqDeleteKey)}>
              Supprimer
            </button>
          </>
        )}
      </p>
      {key.state === 'invalid' && <p className="bad-text">La clé a expiré ou a été refusée : collez une nouvelle clé pour reprendre l'import.</p>}
      <form
        className="soloq-key"
        onSubmit={(e) => {
          e.preventDefault()
          const value = keyInput.trim()
          if (value) act(() => api.soloqSetKey(value).then(() => setKeyInput('')))
        }}
      >
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          aria-label="Clé API Riot"
        />
        <button type="submit" disabled={busy || !keyInput.trim()}>
          {key.state === 'missing' ? 'Enregistrer' : 'Remplacer'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}

      <h3>Échantillon</h3>
      <fieldset className="soloq-settings" disabled={running}>
        <div>
          <strong>Régions</strong>
          <div className="import-grid">
            {overview.platforms.map((p) => (
              <label key={p.key}>
                <input type="checkbox" checked={settings.platforms.includes(p.key)} onChange={() => toggle('platforms', p.key)} /> {p.label}
              </label>
            ))}
          </div>
        </div>
        <div>
          <strong>Rangs</strong>
          <div className="import-grid">
            {overview.tiers.map((t) => (
              <label key={t}>
                <input type="checkbox" checked={settings.tiers.includes(t)} onChange={() => toggle('tiers', t)} /> {RANK_LABEL[t] ?? t}
              </label>
            ))}
          </div>
        </div>
        <p>
          <label>
            Objectif{' '}
            <input type="number" min={10} step={500} value={settings.target} onChange={(e) => set('target', Number(e.target.value))} /> parties
          </label>{' '}
          <label title="Les parties des patchs plus anciens sont supprimées au fil de l'import (0 = tout garder)">
            · garder les{' '}
            <input type="number" min={0} max={100} value={settings.patches_kept} onChange={(e) => set('patches_kept', Number(e.target.value))} /> derniers
            patchs
          </label>{' '}
          <label title="Vide : tous les patchs">
            · patch minimal{' '}
            <input type="text" placeholder="ex. 15.22" size={6} value={settings.min_patch} onChange={(e) => set('min_patch', e.target.value.trim())} />
          </label>{' '}
          <label>
            · historique des joueurs sur{' '}
            <input type="number" min={1} max={730} value={settings.max_age_days} onChange={(e) => set('max_age_days', Number(e.target.value))} /> jours
          </label>
        </p>
      </fieldset>

      {running ? (
        <div>
          <div className="progress" role="progressbar" aria-valuenow={pctDone} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${pctDone}%` }} />
          </div>
          <p>
            {games} / {target} parties · {status.added ?? 0} ajoutées · {status.current ?? 'démarrage…'}
            {status.waiting && (
              <span className="muted">
                {' '}
                · pause de {status.waiting.seconds} s ({status.waiting.reason})
              </span>
            )}
            <button className="link-btn" onClick={() => api.soloqCancel().then(setStatus)}>
              Arrêter
            </button>
          </p>
          {status.resumed && <p className="muted">Import repris là où il s'était arrêté.</p>}
          {status.log && status.log.length > 0 && <pre className="import-log">{status.log.join('\n')}</pre>}
        </div>
      ) : (
        <>
          <p>
            <button disabled={busy || !canStart} onClick={() => act(() => api.soloqStart(settings).then(setStatus))}>
              {stored.games > 0 ? "Reprendre l'import" : "Démarrer l'import"}
            </button>{' '}
            <span className="muted">≈ 25 parties par minute et par région avec une clé personnelle (2 requêtes par partie).</span>
          </p>
          {status.state === 'done' && <p className="good-text">Objectif atteint : {status.games} parties.</p>}
          {status.state === 'exhausted' && (
            <p className="muted">Plus de nouvelles parties pour l'instant ({status.games} en base) : relancez plus tard ou élargissez l'échantillon.</p>
          )}
          {status.state === 'cancelled' && <p className="muted">Import arrêté ; il reprendra là où il en était.</p>}
          {status.state === 'error' && <p className="error">Import interrompu : {status.error}</p>}
        </>
      )}
      <p className="muted">
        L'import respecte la limite des clés personnelles (100 requêtes / 2 min) et peut être arrêté ou relancé à tout moment ; fermé en cours de route,
        il reprend au lancement suivant.
      </p>

      {stored.games > 0 && (
        <table>
          <thead>
            <tr>
              <th>Parties</th>
              <th>Par rang</th>
              <th>Par région</th>
              <th>Patchs</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{stored.games}</td>
              <td>
                {Object.entries(stored.by_tier)
                  .map(([t, n]) => `${RANK_LABEL[t] ?? t} ${n}`)
                  .join(' · ')}
              </td>
              <td>
                {Object.entries(stored.by_platform)
                  .map(([p, n]) => `${p} ${n}`)
                  .join(' · ')}
              </td>
              <td>{stored.patches.join(', ')}</td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  )
}

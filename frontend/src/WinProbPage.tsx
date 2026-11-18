import { useEffect, useMemo, useState } from 'react'
import { api, ROLE_LABEL, type Role, type WpaCoverage, type WpaCurve, type WpaEvent, type WpaPlayerRow, type WpaStatus } from './api'
import { Champ } from './Champ'
import { RANK_LABEL, useScope } from './scope'
import { formatDuration, pct, useAsync, usePersistentState } from './utils'
import { Card, Stat } from './Widgets'
import { clock, curvePoints, eventLabel, eventTooltip, KIND_LABEL, KIND_LETTER, keyMoments, points, SIDE_LABEL, winPct, wBefore } from './winprob'

const TRAIN_COMMAND = 'pip install -r backend/requirements-ml.txt\npython backend/tools/train_wpa.py'
const NO_TIMELINE = "Cette partie n'a pas de timeline : courbe indisponible."
const UNKNOWN_GAME = 'Partie introuvable dans la base solo queue.'

/** Win probability page (REQ-WPA-6, 8, 10, 14): warnings, model, curve of a game, event and champion WPA. */
export function WinProbPage({ gameId }: { gameId?: string }) {
  const { data: status, error } = useAsync(() => api.wpa(), [])
  return (
    <section className="winprob">
      {status && (
        <div className="wp-warnings" role="note">
          {status.warnings.map((w) => (
            <p key={w.key}>⚠ {w.text}</p>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {!status && !error && <p className="muted">Chargement…</p>}
      {status && <ModelCard status={status} />}
      {status?.available && (
        <>
          {gameId ? (
            <GameCurve gameId={gameId} coverage={status.model?.minute_coverage ?? null} />
          ) : (
            <p className="muted">Choisissez une partie dans la page Drafts (lien « Probabilité ») pour voir sa courbe.</p>
          )}
          <Aggregates />
        </>
      )}
    </section>
  )
}

// --- model ------------------------------------------------------------------------------

function ModelCard({ status }: { status: WpaStatus }) {
  const model = status.model
  if (!status.available || !model) {
    return (
      <Card title="Modèle de probabilité de victoire">
        <p className="error">{status.message}</p>
        <pre className="wp-command">{TRAIN_COMMAND}</pre>
      </Card>
    )
  }
  const m = model.metrics
  const num = (v: number | undefined, digits = 3) => (v == null ? '–' : v.toFixed(digits).replace('.', ','))
  return (
    <Card title="Modèle de probabilité de victoire" className="wp-model">
      <div className="wp-model-body">
        <div>
          <p>
            <span className={`badge ${status.status === 'validated' ? 'win' : 'wp-provisional'}`}>
              {status.status === 'validated' ? 'Validé' : 'Provisoire'}
            </span>{' '}
            <span className="badge">{model.source === 'synthetic' ? 'Synthétique' : 'Solo queue'}</span>{' '}
            <span className="muted" title={`Empreinte SHA-256 : ${model.id}`}>
              {model.id.slice(0, 12)}
            </span>
          </p>
          <div className="stat-row small">
            <Stat label="Entraîné le" value={model.trained_at?.slice(0, 10) ?? '–'} />
            <Stat label="Parties" value={model.games?.toLocaleString('fr-FR') ?? '–'} />
            <Stat label="États" value={model.samples?.toLocaleString('fr-FR') ?? '–'} />
            <Stat label="Accuracy" value={pct(m.accuracy, 1)} hint="Part des états de test où le favori du modèle a gagné" />
            <Stat
              label="Log loss"
              value={num(m.log_loss)}
              hint={m.baseline_log_loss != null ? `Régression logistique de référence : ${num(m.baseline_log_loss)}` : undefined}
            />
            <Stat label="Brier" value={num(m.brier)} />
            <Stat label="ECE" value={pct(m.ece, 1)} hint="Erreur de calibration : écart moyen entre probabilité prédite et fréquence observée" />
          </div>
          {status.cache && status.cache.computed < status.cache.total && (
            <p className="muted">
              Calcul en cours : {status.cache.computed} / {status.cache.total} parties
            </p>
          )}
        </div>
        <Reliability bins={m.reliability ?? []} />
      </div>
    </Card>
  )
}

/** Predicted (x) vs observed (y) frequency per probability bin, with the diagonal of a perfect calibration. */
function Reliability({ bins }: { bins: { lo: number; hi: number; n: number; predicted: number; observed: number }[] }) {
  if (!bins.length) return null
  const size = 150
  const max = Math.max(...bins.map((b) => b.n))
  return (
    <figure className="wp-reliability">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label="Courbe de fiabilité">
        <rect x={0} y={0} width={size} height={size} className="wp-frame" />
        <line x1={0} y1={size} x2={size} y2={0} className="wp-diagonal" />
        {bins.map((b) => (
          <circle key={b.lo} cx={b.predicted * size} cy={size - b.observed * size} r={2 + 4 * Math.sqrt(b.n / max)} className="wp-bin">
            <title>{`Prédit ${winPct(b.predicted)} · observé ${winPct(b.observed)} · ${b.n} états`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="muted">Fiabilité : prédit → observé</figcaption>
    </figure>
  )
}

// --- curve of a game --------------------------------------------------------------------

function GameCurve({ gameId, coverage }: { gameId: string; coverage: number | null }) {
  const { data, error, loading } = useAsync(() => api.wpaGame(gameId), [gameId])
  const [selected, setSelected] = useState<number | null>(null)
  const [all, setAll] = usePersistentState('winprob.allEvents', false)
  if (loading) return <p className="muted">Calcul…</p>
  if (error) {
    const text = error.includes('No usable timeline') ? NO_TIMELINE : error.includes('Unknown solo queue game') ? UNKNOWN_GAME : error
    return <p className="muted">{text}</p>
  }
  if (!data) return null
  const rows = all ? data.events : keyMoments(data.events)
  const select = (i: number) => {
    setSelected(i)
    if (!rows.includes(data.events[i])) setAll(true) // show the row of an event outside the key moments
  }
  return (
    <Card title="Courbe de la partie" className="wp-game">
      <p>
        {data.tier && <span className="badge">{RANK_LABEL[data.tier] ?? data.tier}</span>} {formatDuration(data.duration)}
        <span className="muted"> · patch {data.patch ?? '?'} · </span>
        {data.winner ? (
          <span className={data.winner === 1 ? 'wp-blue' : 'wp-red'}>Victoire {SIDE_LABEL[data.winner as 1 | 2]}</span>
        ) : (
          <span className="muted">Vainqueur inconnu</span>
        )}
      </p>
      <Chart curve={data} coverage={coverage} selected={selected} onSelect={select} />
      <div className="wp-table-head">
        <h4>{all ? 'Tous les événements' : 'Moments clés'}</h4>
        <button className="link-btn" onClick={() => setAll(!all)}>
          {all ? 'Moments clés' : 'Tous les événements'}
        </button>
      </div>
      <table className="compact">
        <thead>
          <tr>
            <th>Temps</th>
            <th>Événement</th>
            <th>W Blue</th>
            <th>ΔW</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => {
            const index = data.events.indexOf(e)
            return (
              <tr
                key={index}
                className={`clickable${index === selected ? ' wp-selected' : ''}`}
                onClick={() => setSelected(index)}
                ref={index === selected ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined}
              >
                <td>{clock(e.t)}</td>
                <td className={e.side === 1 ? 'wp-blue' : 'wp-red'}>{eventLabel(e)}</td>
                <td>
                  {winPct(wBefore(e))} → {winPct(e.w)}
                </td>
                <td className={e.dw >= 0 ? 'good-text' : 'bad-text'}>
                  {points(e.dw)} pour {SIDE_LABEL[e.side]}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                Aucun kill ni objectif dans cette partie.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  )
}

const W = 800
const H = 260
const PAD = { left: 40, right: 12, top: 10, bottom: 24 }

function Chart({
  curve,
  coverage,
  selected,
  onSelect,
}: {
  curve: WpaCurve
  coverage: number | null
  selected: number | null
  onSelect: (i: number) => void
}) {
  const [hover, setHover] = useState<number | null>(null)
  const end = Math.max(60, (curve.duration ?? 0) * 60, ...curve.events.map((e) => e.t))
  const x = (t: number) => PAD.left + (t / end) * (W - PAD.left - PAD.right)
  const y = (w: number) => PAD.top + (1 - w) * (H - PAD.top - PAD.bottom)
  const pts = curvePoints(curve.minutes, curve.events)
  const line = pts.map((p) => `${x(p.t).toFixed(1)},${y(p.w).toFixed(1)}`).join(' ')
  const area = pts.length ? `${x(pts[0].t)},${y(0.5)} ${line} ${x(pts[pts.length - 1].t)},${y(0.5)}` : ''
  const ticks = Array.from({ length: Math.floor(end / 300) + 1 }, (_, i) => i * 300)
  const shaded = coverage != null && coverage * 60 < end
  const hovered: WpaEvent | null = hover != null ? curve.events[hover] : null
  return (
    <div className="wp-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Probabilité de victoire de Blue au fil de la partie">
        <defs>
          <clipPath id="wp-above">
            <rect x={0} y={0} width={W} height={y(0.5)} />
          </clipPath>
          <clipPath id="wp-below">
            <rect x={0} y={y(0.5)} width={W} height={H - y(0.5)} />
          </clipPath>
        </defs>
        {shaded && (
          <rect x={x(coverage * 60)} y={PAD.top} width={x(end) - x(coverage * 60)} height={H - PAD.top - PAD.bottom} className="wp-uncovered">
            <title>{`Peu de parties d'entraînement au-delà de ${coverage} min : probabilités moins fiables`}</title>
          </rect>
        )}
        {[0, 0.25, 0.5, 0.75, 1].map((w) => (
          <g key={w}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(w)} y2={y(w)} className={w === 0.5 ? 'wp-mid' : 'wp-grid'} />
            <text x={PAD.left - 6} y={y(w) + 4} className="wp-axis" textAnchor="end">
              {Math.round(w * 100)} %
            </text>
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 6} className="wp-axis" textAnchor="middle">
            {t / 60}
          </text>
        ))}
        <polygon points={area} className="wp-area-blue" clipPath="url(#wp-above)" />
        <polygon points={area} className="wp-area-red" clipPath="url(#wp-below)" />
        <polyline points={line} className="wp-line" />
        {curve.events.map((e, i) => {
          const cls = `wp-marker ${e.side === 1 ? 'blue' : 'red'}${i === selected ? ' selected' : ''}`
          const handlers = { onMouseEnter: () => setHover(i), onMouseLeave: () => setHover(null), onClick: () => onSelect(i) }
          return e.kind === 'kill' ? (
            <circle key={i} cx={x(e.t)} cy={y(e.w)} r={i === selected ? 5 : 3} className={cls} {...handlers} />
          ) : (
            <text key={i} x={x(e.t)} y={y(e.w) - 6} textAnchor="middle" className={cls} {...handlers}>
              {KIND_LETTER[e.kind] ?? '?'}
            </text>
          )
        })}
      </svg>
      {hovered && (
        <div
          className="wp-tooltip"
          style={{ left: `${(x(hovered.t) / W) * 100}%`, top: `${(y(hovered.w) / H) * 100}%` }}
          role="tooltip"
        >
          {eventTooltip(hovered)}
        </div>
      )}
      <p className="muted legend">
        Minutes en abscisse, probabilité de victoire de Blue en ordonnée. Point : kill ; lettre : objectif (T tour, I inhibiteur, P plaque, D dragon, H héraut, L larves, B baron, A Atakhan). Survolez un marqueur pour le détail, cliquez pour le retrouver dans le tableau.
        {shaded && ` Zone grisée : peu de parties d'entraînement au-delà de ${coverage} min, probabilités moins fiables.`}
      </p>
    </div>
  )
}

// --- aggregates -------------------------------------------------------------------------

type SortKey = 'games' | 'wpa' | 'kills_wpa' | 'deaths_wpa'

/** Polls an aggregate every 5 s while the background job is still filling the cache. */
function usePolled<T extends { coverage: WpaCoverage }>(fn: () => Promise<T>, deps: unknown[]) {
  const [tick, setTick] = useState(0)
  const state = useAsync(fn, [...deps, tick])
  const pending = state.data && state.data.coverage.computed < state.data.coverage.total
  useEffect(() => {
    if (!pending) return
    const id = window.setTimeout(() => setTick((t) => t + 1), 5000)
    return () => window.clearTimeout(id)
  }, [pending, state.data])
  return state
}

function CoverageNote({ coverage }: { coverage?: WpaCoverage }) {
  if (!coverage || coverage.computed >= coverage.total) return null
  return (
    <p className="muted computing">
      Calcul en cours : {coverage.computed} / {coverage.total} parties
    </p>
  )
}

function Aggregates() {
  const { filters } = useScope()
  const [by, setBy] = usePersistentState<'champion' | 'player'>('winprob.by', 'champion')
  const [role, setRole] = usePersistentState('winprob.role', '')
  const [minGames, setMinGames] = usePersistentState('winprob.minGames', 5)
  const [sort, setSort] = usePersistentState<SortKey>('winprob.sort', 'wpa')
  const events = usePolled(() => api.wpaEvents(filters), [filters])
  const players = usePolled(
    () => api.wpaChampions(filters, { by, role: role || undefined, min_games: minGames }),
    [filters, by, role, minGames],
  )
  const rows = useMemo(() => [...(players.data?.rows ?? [])].sort((a, b) => b[sort] - a[sort]), [players.data, sort])
  const th = (key: SortKey, label: string, hint: string) => (
    <th className={`sortable${sort === key ? ' sorted' : ''}`} onClick={() => setSort(key)} title={hint}>
      {label}
    </th>
  )
  const name = (r: WpaPlayerRow) => (by === 'champion' ? r.champion! : r.player!)
  return (
    <div className="grid">
      <Card title="Valeur des événements">
        <CoverageNote coverage={events.data?.coverage} />
        {events.error && <p className="error">{events.error}</p>}
        {events.data && (
          <table className="compact">
            <thead>
              <tr>
                <th>Événement</th>
                <th>Nombre</th>
                <th title="Variation moyenne de la probabilité de victoire de l'équipe qui le réalise">ΔW moyen</th>
              </tr>
            </thead>
            <tbody>
              {[{ kind: 'kill', ...events.data.kills }, ...events.data.objectives].map((r) => (
                <tr key={r.kind}>
                  <td>{KIND_LABEL[r.kind] ?? r.kind}</td>
                  <td>{r.n.toLocaleString('fr-FR')}</td>
                  <td>{r.dw == null ? '–' : points(r.dw)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Champions et joueurs">
        <div className="filters">
          <select value={by} onChange={(e) => setBy(e.target.value as 'champion' | 'player')} aria-label="Regrouper par">
            <option value="champion">Champion</option>
            <option value="player">Joueur</option>
          </select>
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Rôle">
            <option value="">Tous rôles</option>
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
          <label>
            Min parties
            <input type="number" min={1} value={minGames} onChange={(e) => setMinGames(Math.max(1, Number(e.target.value) || 1))} />
          </label>
        </div>
        <CoverageNote coverage={players.data?.coverage} />
        {players.error && <p className="error">{players.error}</p>}
        {players.data && rows.length === 0 && (
          <p className="muted">
            Aucun {by === 'champion' ? 'champion' : 'joueur'} avec au moins {minGames} parties dans ce périmètre.
          </p>
        )}
        {rows.length > 0 && (
          <table className="compact">
            <thead>
              <tr>
                <th>{by === 'champion' ? 'Champion' : 'Joueur'}</th>
                {th('games', 'Parties', 'Parties jouées dans le périmètre')}
                {th('wpa', 'WPA / partie', 'Kills + morts, en points de probabilité de victoire par partie')}
                {th('kills_wpa', 'Kills', 'Somme des ΔW de ses kills, par partie')}
                {th('deaths_wpa', 'Morts', 'Somme des ΔW perdus à ses morts, par partie')}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((r) => (
                <tr key={name(r)}>
                  <td>{by === 'champion' ? <Champ name={name(r)} size={24} showName /> : name(r)}</td>
                  <td>{r.games}</td>
                  <td className={r.wpa >= 0 ? 'good-text' : 'bad-text'}>{points(r.wpa)}</td>
                  <td>{points(r.kills_wpa)}</td>
                  <td>{points(r.deaths_wpa)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

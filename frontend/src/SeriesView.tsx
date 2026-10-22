import { useEffect, useState } from 'react'
import { api, type GameData, type Highlight, type HighlightType, type SeriesData, type SeriesQuery, type SideDraft, ROLE_LABEL } from './api'
import { Champ } from './Champ'
import { useScope } from './scope'
import { formatDuration } from './utils'

const HL_ICON: Record<HighlightType, string> = { synergy: '🔗', counter: '⚔', signature: '★', rare: '◆' }

/** "Zeka (Kim Geon-woo)" -> "Zeka" */
const shortName = (name: string | null) => name?.replace(/\s*\(.*\)\s*$/, '') ?? null
const HL_LABEL: Record<HighlightType, string> = {
  synergy: 'Synergie',
  counter: 'Counter de lane',
  signature: 'Signature joueur',
  rare: 'Pick rare',
}

function SideBlock({
  game,
  side,
  focus,
}: {
  game: GameData
  side: 1 | 2
  focus: string[]
}) {
  const data: SideDraft = game.sides[side]
  const mine = game.highlights.filter((h) => h.side === side)
  const roleOrder = ['top', 'jungle', 'mid', 'bot', 'support']
  const picks = [...data.picks].sort((a, b) => roleOrder.indexOf(a.role) - roleOrder.indexOf(b.role))
  const bans = [...data.bans].sort((a, b) => a.order - b.order)
  return (
    <div className={`side-block ${side === 1 ? 'blue' : 'red'}${data.win === false ? ' lost' : ''}`}>
      <div className="side-title">
        <strong>{data.team}</strong>
        <span className="side-tag">{side === 1 ? 'Blue' : 'Red'}</span>
        {side === 1 && <span className="badge first" title="Le côté Blue a la 1ère sélection et le 1er ban">1ʳᵉ sélection · 1er ban</span>}
        {data.win && <span className="badge win">Victoire</span>}
        {data.stats.kills != null && <span className="kills">{data.stats.kills} kills</span>}
      </div>
      <div className="side-body">
        <div className="picks-row">
          {picks.map((p) => {
            const related = mine.filter((h) => h.champions.includes(p.champion))
            return (
              <div key={p.order} className="pick" title={`${ROLE_LABEL[p.role]} — ${p.player ?? ''}`}>
                <Champ
                  name={p.champion}
                  size={44}
                  ring={focus.includes(p.champion) ? 'focus' : undefined}
                  badge={
                    <>
                      <span className="slot-badge" title={`Pick n°${p.slot} de la draft`}>{p.slot}</span>
                      {related.length > 0 && (
                        <span className="hl-badges" title={related.map((h) => h.text).join('\n')}>
                          {[...new Set(related.map((h) => h.type))].map((t) => HL_ICON[t]).join('')}
                        </span>
                      )}
                    </>
                  }
                />
                <span className="player">{shortName(p.player) ?? ROLE_LABEL[p.role]}</span>
                {p.kills != null && (
                  <span className="kda">{p.kills}/{p.deaths}/{p.assists}</span>
                )}
              </div>
            )
          })}
        </div>
        <div className="bans-row" title="Bans dans l'ordre de la draft">
          {bans.map((b) => (
            <Champ
              key={b.order}
              name={b.champion}
              size={26}
              banned
              ring={focus.includes(b.champion) ? 'focus' : undefined}
              badge={<span className="slot-badge small">{b.order}</span>}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function Chips({ highlights }: { highlights: Highlight[] }) {
  if (!highlights.length) return null
  return (
    <div className="chips">
      {highlights.map((h, i) => (
        <span key={i} className={`chip hl-${h.type} side-${h.side}`} title={`${HL_LABEL[h.type]} — ${h.detail}`}>
          {HL_ICON[h.type]} {h.text}
        </span>
      ))}
    </div>
  )
}

function GameRow({ game, focus }: { game: GameData; focus: string[] }) {
  const [showLocked, setShowLocked] = useState(false)
  return (
    <div className={`game-row${game.matched ? '' : ' dim'}`}>
      <div className="game-grid">
        <div className="game-meta">
          <strong>Game {game.game_number ?? '?'}</strong>
          <span>{formatDuration(game.duration)}</span>
          <span className="muted">{game.patch}</span>
        </div>
        <SideBlock game={game} side={1} focus={focus} />
        <SideBlock game={game} side={2} focus={focus} />
      </div>
      <div className="game-extra">
        {game.fearless && game.locked.length > 0 && (
          <button className="link-btn" onClick={() => setShowLocked(!showLocked)}>
            🔒 {game.locked.length} champions indisponibles (Fearless) {showLocked ? '▴' : '▾'}
          </button>
        )}
        <Chips highlights={game.highlights} />
      </div>
      {showLocked && (
        <div className="locked">
          {game.locked.map((c) => (
            <Champ key={c} name={c} size={26} banned />
          ))}
        </div>
      )}
    </div>
  )
}

export function SeriesCard({ series, focus = [] }: { series: SeriesData; focus?: string[] }) {
  const { meta } = useScope()
  const [a, b] = series.teams
  const [sa, sb] = series.score
  const tournament = series.tournament.replace(/^[A-Z]+\//, '')
  const league = meta.selected.length > 1 ? series.league : null
  return (
    <article className="series">
      <header>
        <div className="series-score">
          <span className={sa > sb ? 'winner' : ''}>{a}</span>
          <strong>
            {sa} – {sb}
          </strong>
          <span className={sb > sa ? 'winner' : ''}>{b}</span>
        </div>
        <div className="series-tags">
          {league && <span className="badge league-badge">{league}</span>}
          {series.best_of && <span className="badge">Bo{series.best_of}</span>}
          {series.fearless && (
            <span className="badge fearless" title="Fearless : un champion joué dans une partie ne peut plus être pick par aucune équipe dans la suite de la série">
              Fearless
            </span>
          )}
          {series.stage && <span className="badge">{series.stage}</span>}
          <span className="muted">{tournament}</span>
          <span className="muted">{series.date?.slice(0, 10)}</span>
        </div>
      </header>
      {series.games.map((g) => (
        <GameRow key={g.game_id} game={g} focus={focus} />
      ))}
    </article>
  )
}

/** Paginated list of series, following the global scope plus a query. */
export function SeriesList({ query, focus = [], pageSize = 10 }: { query: SeriesQuery; focus?: string[]; pageSize?: number }) {
  const { filters } = useScope()
  const [items, setItems] = useState<SeriesData[]>([])
  const [total, setTotal] = useState(0)
  const [state, setState] = useState<{ loading: boolean; error?: string }>({ loading: true })
  const queryKey = JSON.stringify([filters, query])

  const load = (offset: number) => {
    setState({ loading: true })
    return api.series(filters, { ...query, limit: pageSize, offset }).then(
      (r) => {
        setItems((prev) => (offset === 0 ? r.series : [...prev, ...r.series]))
        setTotal(r.total)
        setState({ loading: false })
      },
      (e) => setState({ loading: false, error: String(e) }),
    )
  }

  useEffect(() => {
    let alive = true
    setItems([])
    api.series(filters, { ...query, limit: pageSize, offset: 0 }).then(
      (r) => {
        if (!alive) return
        setItems(r.series)
        setTotal(r.total)
        setState({ loading: false })
      },
      (e) => alive && setState({ loading: false, error: String(e) }),
    )
    setState({ loading: true })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey])

  return (
    <div>
      <p className="muted">{total} séries</p>
      {state.error && <p className="error">{state.error}</p>}
      {items.map((s) => (
        <SeriesCard key={s.match_id} series={s} focus={focus} />
      ))}
      {!state.loading && items.length === 0 && <p className="muted">Aucune partie pour ces filtres.</p>}
      {state.loading && <p className="muted">Chargement…</p>}
      {!state.loading && items.length < total && (
        <div className="pager">
          <button onClick={() => load(items.length)}>Charger plus ({total - items.length} restantes)</button>
        </div>
      )}
    </div>
  )
}

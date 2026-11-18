import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  api,
  ApiError,
  ROLES,
  ROLE_LABEL,
  type ItemLevel,
  type ItemsAnalysis,
  type ItemStat,
  type ItemTiming,
  type PathStat,
} from './api'
import { useChampionNames } from './ChampionSelect'
import { fuzzyFilter } from './fuzzy'
import { MultiSelect, type Option } from './MultiSelect'
import { useScope } from './scope'
import { formatDuration, pct, useDebounced, usePersistentState } from './utils'
import { Card, WrBar } from './Widgets'

const MAX_CHAMPIONS = 10

/** Texts of the page, as written in the specification (REQ-ITEM-11). */
const MESSAGES = {
  notSoloq:
    "Les analyses d'objets n'existent qu'en solo queue : Leaguepedia ne donne pas les achats d'objets des parties pro. Choisissez « Solo queue » dans le sélecteur de ligues.",
  noChampion: "Choisissez de 1 à 10 champions pour voir leurs achats d'objets.",
  noGame: 'Aucune partie avec ces champions dans ce périmètre (rôle, joueur, adversaire, rang, patch).',
  noTimeline: (n: number) => `Aucune partie analysable : les ${n} parties trouvées n'ont pas de timeline stockée.`,
  noItem: (n: number) => `Aucun objet acheté au moins ${n} fois.`,
  noBoots: 'Aucune botte achetée.',
  noPath: (n: number) => `Aucun chemin suivi au moins ${n} fois.`,
  unavailable:
    "Données d'objets indisponibles : Data Dragon est injoignable et ces patchs n'ont pas encore été chargés. Réessayez une fois connecté à Internet.",
  notComparable: 'Moins de 10 builds filtrés : la comparaison au build moyen est peu fiable.',
  chance: 'À ce niveau, le hasard seul produirait à peu près autant de trouvailles : elles ne prouvent rien.',
  maxChampions: '10 champions au plus',
  warningTitle: 'Lire ces chiffres avec prudence',
  warning:
    "Un objet est acheté à cause de l'état de la partie : une équipe en avance achète plus tôt et plus cher. Un winrate élevé ne veut donc pas dire que l'objet fait gagner. Chaque winrate est comparé à celui attendu pour le même champion, le même emplacement et le même moment d'achat ; seuls les écarts marqués d'un niveau de fiabilité dépassent le hasard.",
}

const LEVEL_LABEL: Record<ItemLevel, string> = { strong: 'très fiable', likely: 'fiable', hint: 'indice' }
const VERDICT_LABEL = { better: "mieux que l'attendu", worse: "moins bien que l'attendu" } as const

type ListKind = 'slot' | 'boots' | 'paths' | 'items'
type Row = ItemStat | PathStat

interface Ctx {
  data: ItemsAnalysis
  list: ListKind
  filtered: boolean
}

const minutes = (m: number | null | undefined) => (m == null ? '–' : formatDuration(m) || '0:00')
const ordinal = (k: number) => (k === 1 ? '1er' : `${k}e`)

function ItemIcon({ id, data, size = 32 }: { id: number; data: ItemsAnalysis; size?: number }) {
  const [failed, setFailed] = useState(false)
  const name = data.names[String(id)] ?? String(id)
  return (
    <span className="item-icon" title={name}>
      {data.version && !failed ? (
        <img
          src={`https://ddragon.leagueoflegends.com/cdn/${data.version}/img/item/${id}.png`}
          width={size}
          height={size}
          alt={name}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="champ-fallback" style={{ width: size, height: size }} aria-label={name}>
          {name.slice(0, 2)}
        </span>
      )}
    </span>
  )
}

/** Purchases per 2-minute bar; the last bar is 40 minutes and more. */
function Histogram({ timing, height = 20 }: { timing: ItemTiming; height?: number }) {
  const max = Math.max(1, ...timing.histogram)
  const title = timing.histogram
    .map((n, i) => (n ? `${i === timing.histogram.length - 1 ? '40+ min' : `${2 * i}-${2 * i + 2} min`} : ${n}` : ''))
    .filter(Boolean)
    .join('\n')
  return (
    <span className="histogram" style={{ height }} title={title} aria-hidden="true">
      {timing.histogram.map((n, i) => (
        <span key={i} style={{ height: `${(n / max) * 100}%` }} />
      ))}
    </span>
  )
}

/** Columns of every item table; a new statistic per item is one more entry here. */
const ITEM_COLUMNS: {
  key: string
  label: string
  title?: string
  when?: (ctx: Ctx) => boolean
  render: (row: Row, ctx: Ctx) => ReactNode
}[] = [
  {
    key: 'item',
    label: 'Objet',
    render: (row, { data }) =>
      'items' in row ? (
        <span className="item-seq">
          {row.items.map((id, i) => (
            <span key={id}>
              {i > 0 && <span className="muted"> → </span>}
              <ItemIcon id={id} data={data} size={28} />
            </span>
          ))}
        </span>
      ) : (
        <span className="item-name">
          <ItemIcon id={row.item} data={data} />
          <span>{data.names[String(row.item)] ?? row.item}</span>
        </span>
      ),
  },
  { key: 'share', label: 'Part', title: 'Part des builds de la liste', render: (row) => pct(row.share, 1) },
  { key: 'games', label: 'Achats', render: (row) => row.games },
  {
    key: 'avg_slot',
    label: 'Emplacement moyen',
    when: (ctx) => ctx.list === 'items',
    render: (row) => ('avg_slot' in row && row.avg_slot != null ? row.avg_slot.toFixed(2) : '–'),
  },
  {
    key: 'timing',
    label: 'Timing',
    title: '25 % – médiane – 75 % des achats (minutes)',
    render: (row) => (
      <span className="timing">
        {minutes(row.timing.p25)} – <strong>{minutes(row.timing.median)}</strong> – {minutes(row.timing.p75)}
        <Histogram timing={row.timing} height={16} />
      </span>
    ),
  },
  {
    key: 'winrate',
    label: 'Winrate',
    render: (row) => (
      <span
        title={
          row.winrate == null
            ? 'Aucune partie décidée'
            : `${row.wins} victoires sur ${row.decided} parties décidées\nIntervalle à 95 % : ${pct(row.wr_low)} – ${pct(row.wr_high)}\nLissé vers l'attendu : ${pct(row.wr_adjusted)}`
        }
      >
        <WrBar wr={row.winrate} />
      </span>
    ),
  },
  {
    key: 'expected',
    label: 'Attendu',
    title: "Winrate des autres achats du même champion, au même emplacement et dans la même tranche de 5 minutes",
    render: (row) => pct(row.expected),
  },
  {
    key: 'delta',
    label: 'Écart',
    render: (row) =>
      row.delta == null ? (
        <span className="muted">–</span>
      ) : (
        <span className="item-delta">
          {row.delta > 0 ? '+' : ''}
          {Math.round(row.delta * 100)} pts
          {row.level && row.verdict && (
            <span className={`level level-${row.level}`} title={`z = ${row.z}`}>
              {LEVEL_LABEL[row.level]} · {VERDICT_LABEL[row.verdict]}
            </span>
          )}
        </span>
      ),
  },
  { key: 'sold', label: 'Revendu', render: (row) => pct(row.sold) },
  {
    key: 'ref',
    label: 'Build moyen',
    title: 'Part et timing médian dans le build moyen (mêmes champions, rôle et périmètre, sans les filtres joueur et adversaire)',
    when: (ctx) => ctx.filtered,
    render: (row) => (row.ref ? `${pct(row.ref.share, 1)} · ${minutes(row.ref.median)}` : '–'),
  },
]

function ItemTable({ rows, ctx }: { rows: Row[]; ctx: Ctx }) {
  const columns = ITEM_COLUMNS.filter((c) => !c.when || c.when(ctx))
  return (
    <div className="table-scroll">
      <table className="compact item-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} title={c.title}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={'items' in row ? row.items.join('-') : row.item}>
              {columns.map((c) => (
                <td key={c.key}>{c.render(row, ctx)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Summary({ data }: { data: ItemsAnalysis }) {
  const b = data.baseline
  return (
    <>
      <p>
        <strong>{data.builds}</strong> builds analysés · {data.decided} parties décidées
        {data.missing_timeline > 0 && ` · ${data.missing_timeline} partie${data.missing_timeline > 1 ? 's' : ''} sans timeline`} · winrate de base{' '}
        <strong>{pct(b.winrate)}</strong>
        {b.wr_low != null && (
          <small className="muted">
            {' '}
            (intervalle à 95 % : {pct(b.wr_low)} – {pct(b.wr_high)})
          </small>
        )}
      </p>
      {data.reference && (
        <p>
          Build moyen : {data.reference.builds} builds, winrate {pct(data.reference.winrate)}
          {!data.reference.comparable && <span className="bad-text"> — {MESSAGES.notComparable}</span>}
        </p>
      )}
    </>
  )
}

function Slots({ data, ctx }: { data: ItemsAnalysis; ctx: Ctx }) {
  return (
    <div className="item-slots">
      {data.slots.map((s) => (
        <div key={s.slot} className="item-slot">
          <h4>
            {ordinal(s.slot)} objet <small className="muted">— {s.builds} builds · médiane {minutes(s.timing.median)}</small>
          </h4>
          <Histogram timing={s.timing} height={28} />
          {s.items.length ? <ItemTable rows={s.items} ctx={{ ...ctx, list: 'slot' }} /> : <p className="muted">{MESSAGES.noItem(data.min_games)}</p>}
        </div>
      ))}
    </div>
  )
}

function Boots({ data, ctx }: { data: ItemsAnalysis; ctx: Ctx }) {
  const { boots } = data
  return (
    <>
      <p className="muted">
        Sans bottes : {boots.none} builds ({pct(boots.builds ? boots.none / boots.builds : null)})
      </p>
      {boots.items.length ? (
        <ItemTable rows={boots.items} ctx={{ ...ctx, list: 'boots' }} />
      ) : (
        <p className="muted">{boots.none === boots.builds ? MESSAGES.noBoots : MESSAGES.noItem(data.min_games)}</p>
      )}
    </>
  )
}

function Paths({ data, ctx }: { data: ItemsAnalysis; ctx: Ctx }) {
  return (
    <>
      {data.paths.map((p) => (
        <div key={p.length} className="item-paths">
          <h4>
            Chemins de {p.length} objets <small className="muted">— {p.builds} builds</small>
          </h4>
          {p.rows.length ? (
            <>
              <ItemTable rows={p.rows} ctx={{ ...ctx, list: 'paths' }} />
              <p className="muted">Ces chemins couvrent {pct(p.covered)} des builds</p>
            </>
          ) : (
            <p className="muted">{MESSAGES.noPath(data.min_games)}</p>
          )}
        </div>
      ))}
    </>
  )
}

function AllItems({ data, ctx }: { data: ItemsAnalysis; ctx: Ctx }) {
  return data.by_item.length ? (
    <ItemTable rows={data.by_item} ctx={{ ...ctx, list: 'items' }} />
  ) : (
    <p className="muted">{MESSAGES.noItem(data.min_games)}</p>
  )
}

function Reliability({ data }: { data: ItemsAnalysis }) {
  const rows: Row[] = [
    ...data.slots.flatMap((s) => s.items),
    ...data.by_item,
    ...data.boots.items,
    ...data.paths.flatMap((p) => p.rows),
  ]
  return (
    <>
      <p className="muted">
        {data.tested} lignes testées (au moins 12 parties décidées). Le winrate attendu d'une ligne est celui des autres achats du même champion, au
        même emplacement (ou des mêmes bottes, ou des chemins de même longueur) et dans la même tranche de 5 minutes ; l'intervalle à 95 % et
        le winrate lissé (20 parties virtuelles au winrate attendu) sont donnés au survol du winrate. Un niveau exige un écart net (z) et assez de
        parties : très fiable 30, fiable 20, indice 12.
      </p>
      <table className="compact summary-table">
        <thead>
          <tr>
            <th>Fiabilité</th>
            <th>Lignes trouvées</th>
            <th title="Nombre de trouvailles que le seul hasard produirait parmi les lignes testées">Dues au hasard (à peu près)</th>
          </tr>
        </thead>
        <tbody>
          {data.levels.map((lv) => {
            const found = rows.filter((r) => r.level === lv.key).length
            return (
              <tr key={lv.key}>
                <td>
                  <span className={`level level-${lv.key}`}>{LEVEL_LABEL[lv.key]}</span>
                </td>
                <td>{found}</td>
                <td className="muted">
                  {lv.by_chance.toFixed(1)}
                  {found > 0 && found <= 2 * lv.by_chance && <span className="bad-text"> — {MESSAGES.chance}</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

/** Cards of the results, in the order of the specification; a new card is one more entry here. */
const PANELS: { id: string; title: string; wide?: boolean; render: (data: ItemsAnalysis, ctx: Ctx) => ReactNode }[] = [
  { id: 'summary', title: 'Résumé', render: (data) => <Summary data={data} /> },
  { id: 'slots', title: "Ordre d'achat", wide: true, render: (data, ctx) => <Slots data={data} ctx={ctx} /> },
  { id: 'boots', title: 'Bottes', wide: true, render: (data, ctx) => <Boots data={data} ctx={ctx} /> },
  { id: 'paths', title: 'Chemins de build', wide: true, render: (data, ctx) => <Paths data={data} ctx={ctx} /> },
  { id: 'items', title: 'Tous les objets', wide: true, render: (data, ctx) => <AllItems data={data} ctx={ctx} /> },
  { id: 'reliability', title: 'Fiabilité', render: (data) => <Reliability data={data} /> },
]

function Results({ data }: { data: ItemsAnalysis }) {
  if (data.builds === 0) {
    return <p className="muted">{data.missing_timeline > 0 ? MESSAGES.noTimeline(data.missing_timeline) : MESSAGES.noGame}</p>
  }
  const ctx: Ctx = { data, list: 'slot', filtered: !!(data.player || data.vs) }
  return (
    <div className="item-panels">
      {PANELS.map((p) => (
        <Card key={p.id} title={p.title} className={p.wide ? 'wide' : ''}>
          {p.render(data, ctx)}
        </Card>
      ))}
    </div>
  )
}

function Analysis() {
  const { filters } = useScope()
  const { names, popularity } = useChampionNames()
  const [champions, setChampions] = usePersistentState<string[]>('items.champions', [])
  const [role, setRole] = usePersistentState('items.role', '')
  const [player, setPlayer] = usePersistentState('items.player', '')
  const [vs, setVs] = usePersistentState('items.vs', '')
  const [minGames, setMinGames] = usePersistentState('items.minGames', 5)
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{ key?: string; data?: ItemsAnalysis; error?: Error }>({})

  const request = useDebounced(
    JSON.stringify({ filters, query: { champion: champions, role: role || undefined, player: player || undefined, vs: vs || undefined, min_games: minGames } }),
    300,
  )
  const key = `${attempt}|${request}`
  useEffect(() => {
    const { filters: f, query } = JSON.parse(request)
    if (!query.champion.length) return
    let alive = true
    api.items(f, query).then(
      (data) => alive && setState({ key, data }),
      (error: Error) => alive && setState((s) => ({ key, data: s.data, error })),
    )
    return () => {
      alive = false
    }
  }, [key, request])
  const loading = champions.length > 0 && state.key !== key

  // The player and matchup filters belong to the chosen champions and role.
  const changeChampions = (next: string[]) => {
    setChampions(next.slice(0, MAX_CHAMPIONS))
    setPlayer('')
    setVs('')
  }
  const changeRole = (next: string) => {
    setRole(next)
    setPlayer('')
    setVs('')
  }

  const championOptions = useMemo<Option[]>(
    () => [...names].sort((a, b) => (popularity[b] ?? 0) - (popularity[a] ?? 0) || a.localeCompare(b)).map((n) => ({ value: n, label: n })),
    [names, popularity],
  )
  const filterChampions = useMemo(
    () => (query: string, options: Option[]) => {
      const byLabel = new Map(options.map((o) => [o.label, o]))
      return fuzzyFilter(query, [...byLabel.keys()]).map((label) => byLabel.get(label)!)
    },
    [],
  )
  const data = state.data
  const players = data?.options.players ?? []
  const opponents = data?.options.opponents ?? []
  const error = loading ? undefined : state.error

  return (
    <>
      <div className="filters">
        <MultiSelect
          label="Champions"
          allLabel="Champions…"
          noun={['champion', 'champions']}
          options={championOptions}
          value={champions}
          onChange={changeChampions}
          max={MAX_CHAMPIONS}
          maxTitle={MESSAGES.maxChampions}
          filter={filterChampions}
        />
        <select value={role} onChange={(e) => changeRole(e.target.value)} aria-label="Rôle">
          <option value="">Tous les rôles</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
        <select value={player} onChange={(e) => setPlayer(e.target.value)} aria-label="Joueur">
          <option value="">Tous les joueurs</option>
          {player && !players.some((p) => p.player === player) && <option value={player}>{player}</option>}
          {players.map((p) => (
            <option key={p.player} value={p.player}>
              {p.player} ({p.games})
            </option>
          ))}
        </select>
        <select value={vs} onChange={(e) => setVs(e.target.value)} aria-label="Adversaire">
          <option value="">Tous les adversaires</option>
          {vs && !opponents.some((o) => o.champion === vs) && <option value={vs}>{vs}</option>}
          {opponents.map((o) => (
            <option key={o.champion} value={o.champion}>
              {o.champion} ({o.games})
            </option>
          ))}
        </select>
        <label>
          Min achats
          <input type="number" min={1} value={minGames} onChange={(e) => setMinGames(Math.max(1, Number(e.target.value) || 1))} />
        </label>
      </div>

      <section className="card items-warning" role="note" aria-labelledby="items-warning-title">
        <h3 id="items-warning-title">{MESSAGES.warningTitle}</h3>
        <p>{MESSAGES.warning}</p>
      </section>

      {champions.length === 0 ? (
        <p className="muted">{MESSAGES.noChampion}</p>
      ) : (
        <>
          {error instanceof ApiError && error.status === 503 ? (
            <p className="error">
              {MESSAGES.unavailable}{' '}
              <button type="button" onClick={() => setAttempt((n) => n + 1)}>
                Réessayer
              </button>
            </p>
          ) : (
            error && <p className="error">Erreur : {error.message}</p>
          )}
          {loading && <p className="muted">Chargement…</p>}
          {data && !error && (
            <div className={`results${loading ? ' stale' : ''}`}>
              <Results data={data} />
            </div>
          )}
        </>
      )}
    </>
  )
}

/** Item purchases of 1 to 10 champions in solo queue (REQ-ITEM-1 to 11). */
export function ItemsPage() {
  const { meta } = useScope()
  return <section className="items-page">{meta.soloq ? <Analysis /> : <p className="muted">{MESSAGES.notSoloq}</p>}</section>
}

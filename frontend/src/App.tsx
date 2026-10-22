import { useEffect, useMemo, useRef, useState } from 'react'
import { api, normalizeFilters, selectedLeagues, type Filters, type ListFilter, type Meta } from './api'
import { BuilderPage } from './BuilderPage'
import { ChampionPage } from './ChampionPage'
import { ChampionsPage } from './ChampionsPage'
import { CounteredPage } from './CounteredPage'
import { DraftsPage } from './DraftsPage'
import { DataPage } from './DataPage'
import { DuoPage } from './DuoPage'
import { FilterBar } from './FilterBar'
import { PairsPage } from './PairsPage'
import { RecordsPage } from './RecordsPage'
import { href, useRoute } from './router'
import { ScopeContext } from './scope'
import { SeriesList } from './SeriesView'
import { useAsync } from './utils'

const TABS = [
  { id: 'drafts', label: 'Drafts' },
  { id: 'champions', label: 'Champions' },
  { id: 'pairs', label: 'Paires' },
  { id: 'countered', label: 'Synergies contrées' },
  { id: 'records', label: 'Records' },
  { id: 'builder', label: 'Suggestions / Tracker' },
  { id: 'data', label: 'Données' },
]

const STORAGE_KEY = 'lck-scope'

function loadFilters(): Filters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === null ? { ...DEFAULT_SCOPE } : normalizeFilters(JSON.parse(raw))
  } catch {
    return {}
  }
}

/** First visit: the latest season (filled in once the data is known). */
const DEFAULT_SCOPE: Filters = { year: ['latest'] }

function Page({ parts, meta }: { parts: string[]; meta: Meta }) {
  const [page = 'drafts', a, b, c] = parts
  switch (page) {
    case 'champion':
      return a ? <ChampionPage key={a} name={a} /> : <ChampionsPage />
    case 'duo':
      return b && c ? <DuoPage kind={a as 'synergy' | 'counter'} a={b} b={c} /> : <DraftsPage />
    case 'game':
      return a ? <SeriesList query={{ game: a }} pageSize={1} /> : <DraftsPage />
    case 'champions':
      return <ChampionsPage />
    case 'pairs':
      return <PairsPage />
    case 'countered':
      return <CounteredPage />
    case 'records':
      return <RecordsPage />
    case 'builder':
      return <BuilderPage />
    case 'data':
      return <DataPage meta={meta} />
    default:
      return <DraftsPage />
  }
}

function Shell({ meta, filters, setFilters }: { meta: Meta; filters: Filters; setFilters: (f: Filters) => void }) {
  const route = useRoute()
  const scope = useMemo(() => ({ meta, filters, setFilters }), [meta, filters, setFilters])
  const current = route.parts[0] === 'champion' ? 'champions' : (route.parts[0] ?? 'drafts')
  return (
    <ScopeContext.Provider value={scope}>
      <header className="top">
        <h1>
          LoL Pro Comp <small className="version">v{meta.version}</small>
        </h1>
        <nav>
          {TABS.map((t) => (
            <a key={t.id} href={href(t.id)} className={current === t.id || (t.id === 'drafts' && current === 'game') ? 'active' : ''}>
              {t.label}
            </a>
          ))}
        </nav>
        <span className="muted">
          {meta.selected.map((k) => meta.leagues.find((l) => l.key === k)?.name ?? k).join(' + ')} · {meta.games} parties · {meta.series} séries
        </span>
      </header>
      <FilterBar />
      <Page key={meta.selected.join(',')} parts={route.parts} meta={meta} />
    </ScopeContext.Provider>
  )
}

/** Drops team/tournament/year/patch values that do not exist in the newly selected leagues. */
function pruneFilters(f: Filters, meta: Meta): Filters {
  const valid: Partial<Record<ListFilter, Set<string>>> = {
    team: new Set(meta.teams),
    tournament: new Set(meta.tournaments.map((t) => t.name)),
    year: new Set(meta.years),
    patch: new Set(meta.patches),
  }
  let changed = false
  const next: Filters = { ...f }
  for (const key of Object.keys(valid) as ListFilter[]) {
    const current = f[key]
    if (!current) continue
    const kept = current.filter((v) => valid[key]!.has(v))
    if (kept.length !== current.length) {
      changed = true
      next[key] = kept.length ? kept : undefined
    }
  }
  return changed ? next : f
}

export default function App() {
  const [filters, setFilters] = useState<Filters>(loadFilters)
  useEffect(() => localStorage.setItem(STORAGE_KEY, JSON.stringify(filters)), [filters])
  const leagues = selectedLeagues(filters)
  const leaguesKey = leagues.join(',')
  const { data: meta, error } = useAsync(() => api.meta(leagues), [leaguesKey])
  const ready = !!meta && [...meta.selected].sort().join(',') === [...leagues].sort().join(',')

  // In the desktop app the window pings the server, which quits once the window is closed.
  const desktop = meta?.desktop
  useEffect(() => {
    if (!desktop) return
    api.heartbeat()
    const id = window.setInterval(api.heartbeat, 5000)
    return () => window.clearInterval(id)
  }, [desktop])
  // Keep the previous scope on screen while another league set loads, so open menus are not torn down.
  const shown = useRef<Meta | null>(null)
  if (ready) shown.current = meta

  useEffect(() => {
    if (!meta || !ready) return
    // Nothing for the selected leagues but data for others (e.g. only the LPL was imported): switch to those.
    const present = meta.leagues.filter((l) => l.games > 0)
    if (meta.games === 0 && present.length > 0) {
      setFilters((f) => ({ ...f, league: [present[0].key] }))
      return
    }
    if (meta.games === 0) return // keep the first-visit default ("latest year") until there is data
    // Resolve the "latest" placeholder of a first visit, then drop values the new leagues do not have.
    setFilters((f) => {
      const resolved = f.year?.includes('latest') ? { ...f, year: meta.years.length ? [meta.years[0]] : undefined } : f
      return pruneFilters(resolved, meta)
    })
  }, [meta, ready])

  return (
    <div className="app">
      {error && (
        <p className="error">
          Données indisponibles pour {leaguesKey} : {error}{' '}
          <button className="link-btn" onClick={() => setFilters({})}>
            Revenir à la ligue par défaut
          </button>
        </p>
      )}
      {meta && meta.games === 0 && <DataPage meta={meta} />}
      {shown.current && shown.current.games > 0 && !filters.year?.includes('latest') && (
        <Shell meta={shown.current} filters={filters} setFilters={setFilters} />
      )}
      {!ready && !error && <p className="muted">Chargement de {leaguesKey}…</p>}
    </div>
  )
}

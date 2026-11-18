import { type ListFilter } from './api'
import { MultiSelect, type Option } from './MultiSelect'
import { filtersForScope, RANK_LABEL, selectLeagues, SOLOQ, useScope } from './scope'

const LABELS: Record<ListFilter, string> = {
  league: 'Ligue',
  team: 'Équipe',
  tournament: 'Tournoi',
  year: 'Année',
  patch: 'Patch',
  game_number: 'Game',
  rank: 'Rang',
}

export function FilterBar() {
  const { meta, filters, setFilters } = useScope()
  // Solo queue games have no team, series or Fearless; their "tournament" is the region (EUW, KR...).
  const soloq = meta.soloq

  // The league list can never be empty: the default league is used then.
  const setList = (key: ListFilter) => (value: string[]) =>
    setFilters({ ...filters, [key]: value.length ? value : undefined })
  const remove = (key: ListFilter, value: string) => setList(key)((filters[key] ?? []).filter((v) => v !== value))
  const setLeagues = (value: string[]) => {
    const next = selectLeagues(filters.league ?? [], value)
    setFilters({ ...filtersForScope(filters, next.includes(SOLOQ)), league: next.length ? next : undefined })
  }

  const leagues: Option[] = meta.leagues
    .filter((l) => l.games > 0)
    .map((l) => ({ value: l.key, label: l.name, detail: `${l.region} · ${l.games}`, badge: l.key === SOLOQ ? 'Riot API' : undefined }))
  const teams: Option[] = meta.teams.map((t) => ({ value: t, label: t }))
  const years: Option[] = meta.years.map((y) => ({ value: y, label: y }))
  const tournaments: Option[] = meta.tournaments.map((t) => ({ value: t.name, label: t.name, badge: t.fearless ? 'Fearless' : undefined }))
  const patches: Option[] = meta.patches.map((p) => ({ value: p, label: p }))
  const games: Option[] = [1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: `Game ${n}` }))
  const ranks: Option[] = meta.ranks.map((r) => ({ value: r, label: RANK_LABEL[r] ?? r }))

  const label = (key: ListFilter) => (soloq && key === 'tournament' ? 'Région' : LABELS[key])
  const chipKeys = (Object.keys(LABELS) as ListFilter[]).filter((k) => k !== 'league')
  const chips = chipKeys.flatMap((key) => (filters[key] ?? []).map((value) => ({ key, value })))
  const active = chips.length > 0 || filters.fearless !== undefined

  return (
    <div className="scope">
      <div className="filters">
        <MultiSelect label="Ligues" allLabel="LCK" noun={['ligue', 'ligues']} options={leagues} value={filters.league ?? []} onChange={setLeagues} />
        {!soloq && (
          <MultiSelect label="Équipes" allLabel="Toutes les équipes" noun={['équipe', 'équipes']} options={teams} value={filters.team ?? []} onChange={setList('team')} />
        )}
        {soloq && (
          <MultiSelect label="Rangs" allLabel="Tous les rangs" noun={['rang', 'rangs']} options={ranks} value={filters.rank ?? []} onChange={setList('rank')} />
        )}
        <MultiSelect label="Années" allLabel="Toutes les années" noun={['année', 'années']} options={years} value={filters.year ?? []} onChange={setList('year')} />
        {soloq ? (
          <MultiSelect label="Régions" allLabel="Toutes les régions" noun={['région', 'régions']} options={tournaments} value={filters.tournament ?? []} onChange={setList('tournament')} />
        ) : (
          <MultiSelect label="Tournois" allLabel="Tous les tournois" noun={['tournoi', 'tournois']} options={tournaments} value={filters.tournament ?? []} onChange={setList('tournament')} />
        )}
        <MultiSelect label="Patchs" allLabel="Tous les patchs" noun={['patch', 'patchs']} options={patches} value={filters.patch ?? []} onChange={setList('patch')} />
        {!soloq && (
          <>
            <MultiSelect label="Games de la série" allLabel="Toutes les games" noun={['game', 'games']} options={games} value={filters.game_number ?? []} onChange={setList('game_number')} />
            <select
              value={filters.fearless ?? ''}
              onChange={(e) => setFilters({ ...filters, fearless: e.target.value || undefined })}
              aria-label="Format de draft"
            >
              <option value="">Fearless + classique</option>
              <option value="true">Fearless uniquement{meta.fearless_since ? ` (depuis ${meta.fearless_since})` : ''}</option>
              <option value="false">Draft classique uniquement</option>
            </select>
          </>
        )}
        {active && (
          <button className="link-btn" onClick={() => setFilters({ league: filters.league })}>
            Tout réinitialiser
          </button>
        )}
      </div>
      {chips.length > 0 && (
        <div className="filter-chips">
          {chips.map(({ key, value }) => (
            <button key={`${key}:${value}`} className="chip-btn" onClick={() => remove(key, value)} title="Retirer ce filtre">
              <span className="muted">{label(key)}</span> {key === 'rank' ? (RANK_LABEL[value] ?? value) : value} ✕
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

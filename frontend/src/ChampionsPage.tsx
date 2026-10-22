import { useMemo, useState } from 'react'
import { api, ROLE_LABEL, type ChampionRow, type Role } from './api'
import { Champ } from './Champ'
import { ChampionSelect } from './ChampionSelect'
import { navigate } from './router'
import { useScope } from './scope'
import { pct, useAsync } from './utils'
import { TierBadge } from './Widgets'

type SortKey = 'strength' | 'presence' | 'picks' | 'bans' | 'winrate' | 'wr_adjusted' | 'pick_rate' | 'ban_rate'

export function ChampionsPage() {
  const { filters } = useScope()
  const [sort, setSort] = useState<SortKey>('strength')
  const [role, setRole] = useState('')
  const [minGames, setMinGames] = useState(1)
  const { data, loading, error } = useAsync(() => api.champions(filters), [filters])

  const rows = useMemo(() => {
    const list = (data ?? [])
      .filter((r) => r.picks >= minGames || r.bans >= minGames)
      .filter((r) => !role || (r.roles[role as Role] ?? 0) > 0)
    const val = (r: ChampionRow) =>
      (sort === 'strength' ? r.strength.score : sort === 'wr_adjusted' ? r.strength.wr_adjusted : (r[sort] ?? -1)) as number
    return [...list].sort((a, b) => val(b) - val(a))
  }, [data, sort, role, minGames])

  const th = (key: SortKey, label: string) => (
    <th className={`sortable${sort === key ? ' sorted' : ''}`} onClick={() => setSort(key)}>
      {label}
    </th>
  )

  return (
    <section>
      <div className="filters">
        <ChampionSelect value="" onChange={(n) => n && navigate('champion', n)} placeholder="Aller à un champion…" clearOnPick />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Rôle">
          <option value="">Tous rôles</option>
          {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
        <label>
          Min
          <input type="number" min={1} value={minGames} onChange={(e) => setMinGames(Math.max(1, Number(e.target.value) || 1))} />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Chargement…</p>}
      <table>
        <thead>
          <tr>
            <th>Champion</th>
            <th>Rôles</th>
            {th('strength', 'Force')}
            {th('presence', 'Présence')}
            {th('picks', 'Picks')}
            {th('pick_rate', 'Pick %')}
            {th('bans', 'Bans')}
            {th('ban_rate', 'Ban %')}
            {th('winrate', 'Winrate')}
            {th('wr_adjusted', 'WR ajusté')}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((r) => (
            <tr key={r.champion}>
              <td>
                <Champ name={r.champion} size={30} showName />
              </td>
              <td className="muted">
                {Object.entries(r.roles)
                  .slice(0, 2)
                  .map(([k, n]) => `${ROLE_LABEL[k as Role]} (${n})`)
                  .join(' · ')}
              </td>
              <td>
                <TierBadge strength={r.strength} />
              </td>
              <td>{pct(r.presence)}</td>
              <td>{r.picks}</td>
              <td>{pct(r.pick_rate)}</td>
              <td>{r.bans}</td>
              <td>{pct(r.ban_rate)}</td>
              <td>{pct(r.winrate)}</td>
              <td title={`Intervalle à 95 % : ${pct(r.strength.wr_low)} – ${pct(r.strength.wr_high)}`}>
                {pct(r.strength.wr_adjusted)}
                {r.strength.significant_edge && <span className="good-text" title="Avantage statistiquement significatif"> ✓</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

import { api, ROLE_LABEL } from './api'
import { Champ } from './Champ'
import { DenialCard } from './DenialCard'
import { href } from './router'
import { useScope } from './scope'
import { SeriesList } from './SeriesView'
import { useAsync } from './utils'
import { Stat, WrBar } from './Widgets'

export function DuoPage({ kind, a, b }: { kind: 'synergy' | 'counter'; a: string; b: string }) {
  const { filters } = useScope()
  const { data: d, error } = useAsync(() => api.duo(filters, kind, a, b), [kind, a, b, filters])
  const synergy = kind === 'synergy'
  return (
    <section>
      <div className="champ-head">
        <Champ name={a} size={72} />
        <span className="vs-big">{synergy ? '+' : 'vs'}</span>
        <Champ name={b} size={72} />
        <div>
          <h2>
            {a} {synergy ? '+' : 'vs'} {b}
          </h2>
          <div className="muted">
            {synergy ? 'Picks par la même équipe' : 'Picks par des équipes adverses'} ·{' '}
            <a href={href('duo', synergy ? 'synergy' : 'counter', b, a)}>inverser</a>
          </div>
        </div>
        {d && (
          <div className="stat-row">
            <Stat label="Parties" value={d.games} />
            <Stat label={`Victoires de l'équipe avec ${a}`} value={`${d.wins} / ${d.games}`} />
            <Stat label="Winrate" value={<WrBar wr={d.winrate} />} />
          </div>
        )}
      </div>
      {d && d.roles.length > 0 && (
        <p className="muted">
          Rôles :{' '}
          {d.roles.map((r) => `${a} ${ROLE_LABEL[r.a_role]} / ${b} ${ROLE_LABEL[r.b_role]} (${r.games})`).join(' · ')}
        </p>
      )}
      {synergy && <DenialCard a={a} b={b} />}
      {error && <p className="error">{error}</p>}
      <h3>Parties concernées</h3>
      <SeriesList query={synergy ? { with: `${a},${b}` } : { versus: `${a},${b}` }} focus={[a, b]} pageSize={6} />
    </section>
  )
}

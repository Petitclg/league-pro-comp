import { api, ROLE_LABEL, type DuoDetail } from './api'
import { Champ } from './Champ'
import { DenialCard } from './DenialCard'
import { href } from './router'
import { useScope } from './scope'
import { SeriesList } from './SeriesView'
import { pct, useAsync } from './utils'
import { Card, Stat, WrBar } from './Widgets'

const OUTCOMES = [
  ['realised', 'jouée', 'open-realised'],
  ['unused', 'disponible mais non prise', 'open-unused'],
  ['taken', "contrée : prise par l'adversaire", 'open-taken'],
  ['banned', 'contrée : bannie', 'open-banned'],
] as const

/** What the teams did each time the duo was open (one member picked, the other still available). */
function OpenCard({ a, b, rows }: { a: string; b: string; rows: NonNullable<DuoDetail['open']> }) {
  const shown = rows.filter((r) => r.open + r.taken + r.banned > 0)
  if (!shown.length) return null
  return (
    <Card title="Quand la paire est ouverte" className="open-card">
      <p className="muted">
        La paire est <em>ouverte</em> quand une équipe prend {a} ou {b} et que l'autre est encore disponible au moment où elle remplit son
        poste : l'équipe la prend alors (jouée) ou pick autre chose. Si l'adversaire a pris ou banni le partenaire avant, la paire est
        <em> contrée</em> : elle n'était pas ouverte et n'entre pas dans le taux. 100 % = les équipes la prennent systématiquement
        dès qu'elles le peuvent.
      </p>
      {shown.map((r) => (
        <div key={`${r.a_role}${r.b_role}`} className="open-row">
          <strong>
            {a} {ROLE_LABEL[r.a_role]} + {b} {ROLE_LABEL[r.b_role]}
          </strong>
          <span>
            jouée <strong>{pct(r.rate)}</strong> du temps quand elle est ouverte ({r.realised} / {r.open})
            {r.taken + r.banned > 0 && <> · contrée avant {r.taken + r.banned} fois</>}
            {r.winrate != null && (
              <>
                {' '}· winrate quand elle est jouée <strong>{pct(r.winrate)}</strong>
              </>
            )}
          </span>
          <span className="open-bar" role="img" aria-label="Répartition des cas où la paire était ouverte">
            {OUTCOMES.map(([key, label, cls]) =>
              r[key] > 0 ? (
                <span key={key} className={cls} style={{ width: `${(r[key] / (r.open + r.taken + r.banned)) * 100}%` }} title={`${label} : ${r[key]}`} />
              ) : null,
            )}
          </span>
          <small className="muted">
            {OUTCOMES.map(([key, label]) => `${label} ${r[key]}`).join(' · ')}
          </small>
        </div>
      ))}
    </Card>
  )
}

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
      {synergy && d?.open && <OpenCard a={a} b={b} rows={d.open} />}
      {synergy && <DenialCard a={a} b={b} />}
      {error && <p className="error">{error}</p>}
      <h3>Parties concernées</h3>
      <SeriesList query={synergy ? { with: `${a},${b}` } : { versus: `${a},${b}` }} focus={[a, b]} pageSize={6} />
    </section>
  )
}

import { api, type DenialDirection, type DenialMeasure, type DenialSummary, type NeutralGroup, type Neutralisation } from './api'
import { Champ } from './Champ'
import { useScope } from './scope'
import { formatLift, pct, useAsync } from './utils'

const SEGMENTS = [
  { key: 'banned_before', label: 'Banni avant le pick', cls: 'seg-ban-before' },
  { key: 'deny_pick', label: 'Pické en face (deny)', cls: 'seg-deny' },
  { key: 'ban_after', label: 'Banni après le pick', cls: 'seg-ban-after' },
  { key: 'untouched', label: 'Ignoré', cls: 'seg-untouched' },
  { key: 'combo', label: 'Combo réalisé', cls: 'seg-combo' },
] as const

const MEASURES = [
  { key: 'anticipation', label: 'Anticipation', hint: 'Partenaire déjà banni quand le champion est pick' },
  { key: 'reaction', label: 'Réaction', hint: 'Partenaire pické ou banni en face après le pick' },
  { key: 'removed', label: 'Total retiré', hint: 'Anticipation + réaction' },
] as const

function verdict(m: DenialMeasure, combo = false): { text: string; cls: string } {
  if (m.lift == null || m.expected_rate == null) return { text: '–', cls: 'muted' }
  if (combo) {
    if (m.z >= 1.64 && m.lift >= 1.2) return { text: 'plus joué ensemble que d\'habitude', cls: 'good-text' }
    if (m.z <= -1.64 && m.lift <= 0.8) return { text: 'moins joué ensemble', cls: 'bad-text' }
    return { text: 'normal', cls: 'muted' }
  }
  if (m.z >= 2.33 && m.lift >= 1.3) return { text: 'souvent cassée', cls: 'bad-text' }
  if (m.z >= 1.64 && m.lift >= 1.2) return { text: 'plutôt cassée', cls: 'bad-text' }
  if (m.z <= -1.64 && m.lift <= 0.8) return { text: 'rarement contrée', cls: 'good-text' }
  return { text: 'normal', cls: 'muted' }
}

function MeasureRow({ label, hint, m, combo = false }: { label: string; hint: string; m: DenialMeasure; combo?: boolean }) {
  const v = verdict(m, combo)
  return (
    <tr title={`${hint}. z = ${m.z}`}>
      <td>{label}</td>
      <td>
        <strong>{pct(m.rate)}</strong> <small className="muted">({m.count})</small>
      </td>
      <td className="muted">{pct(m.expected_rate)}</td>
      <td>{formatLift(m.lift)}</td>
      <td className={v.cls}>{v.text}</td>
    </tr>
  )
}

function Bar({ s }: { s: DenialSummary }) {
  return (
    <>
      <div className="outcome-bar">
        {SEGMENTS.map((seg) => {
          const n = s.outcomes[seg.key]
          return n ? (
            <span
              key={seg.key}
              className={seg.cls}
              style={{ flexGrow: n }}
              title={`${seg.label} : ${n} (${pct(n / s.available)})`}
            >
              {n / s.available >= 0.08 ? pct(n / s.available) : ''}
            </span>
          ) : null
        })}
      </div>
      <div className="outcome-legend">
        {SEGMENTS.map((seg) => (
          <span key={seg.key}>
            <i className={seg.cls} /> {seg.label} <small className="muted">{s.outcomes[seg.key]}</small>
          </span>
        ))}
      </div>
    </>
  )
}

export function neutralVerdict(g: NeutralGroup): { text: string; cls: string } {
  const excess = Math.round(g.expected_both_banned - g.both_banned)
  if (g.n < 20) return { text: 'échantillon trop petit', cls: 'muted' }
  if (g.z >= 3.3 && excess >= 8) return { text: 'souvent neutralisée par un ban', cls: 'bad-text' }
  if (g.z >= 2.33 && excess >= 8) return { text: 'probablement neutralisée', cls: 'bad-text' }
  return { text: 'pas de signe net', cls: 'muted' }
}

/** One sentence per game group: how often one of the two is banned, and what that means. */
export function NeutralisationBlock({ data }: { data: Neutralisation }) {
  const { a, b } = data
  return (
    <div className="neutral-block">
      <h4 className="denial-title">
        Avant même les picks : <Champ name={a} size={26} showName /> ou <Champ name={b} size={26} showName /> est-il banni ?
      </h4>
      <div className="neutral-groups">
        {data.groups.slice(0, 2).map((g) => {
          const v = neutralVerdict(g)
          return (
            <div key={g.label} className="neutral-group">
              <strong>{g.label}</strong> <small className="muted">({g.n} parties)</small>{' '}
              <span className={v.cls}>{v.text}</span>
              {g.n < 20 ? null : (
                <>
                  <p>
                    {a} ou {b} est banni dans <strong>{pct(g.either_rate)}</strong> des parties ({g.either_banned} sur {g.n}). Si les bans
                    des deux étaient indépendants (au même patch), on en attendrait {pct(g.expected_either_rate)}.
                  </p>
                  <p>
                    Ils sont bannis <em>tous les deux</em> dans {g.both_banned} parties seulement, contre {g.expected_both_banned} attendues :{' '}
                    {g.expected_both_banned - g.both_banned >= 3
                      ? 'les équipes en bannissent un OU l\'autre, rarement les deux.'
                      : 'pas de différence notable.'}
                  </p>
                  <p className="muted">
                    Quand aucun des deux n'est banni ({g.free} parties) : joués ensemble {g.together}, dans des équipes adverses {g.split},
                    un seul pické {g.one_picked}, aucun {g.none_picked}.
                  </p>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Direction({ d }: { d: DenialDirection }) {
  if (d.available < 10) {
    return (
      <div className="denial-dir">
        <h4 className="denial-title">
          <Champ name={d.trigger} size={26} showName /> → <Champ name={d.partner} size={26} showName />
        </h4>
        <p className="muted">Pas assez de parties où {d.trigger} est pick avec {d.partner} encore disponible ({d.available}).</p>
      </div>
    )
  }
  const excluded = d.outcomes.prior_combo + d.outcomes.taken_before
  return (
    <div className="denial-dir">
      <h4 className="denial-title">
        Quand <Champ name={d.trigger} size={26} showName /> est pick, que devient <Champ name={d.partner} size={26} showName /> ?
      </h4>
      <p className="muted">
        {d.available} parties où {d.partner} était encore disponible
        {excluded > 0 && ` (${excluded} écartées : déjà pické avant)`}.
      </p>
      <Bar s={d} />
      <table className="compact denial-table">
        <thead>
          <tr>
            <th></th>
            <th>Observé</th>
            <th title="Ce qui arrive à ce champion avec un autre champion en trigger, au même patch, au même moment de la draft et dans le même type de game">Attendu</th>
            <th>Écart</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {MEASURES.map((m) => (
            <MeasureRow key={m.key} label={m.label} hint={m.hint} m={d.measures[m.key]} />
          ))}
          <MeasureRow label="Combo réalisé" hint="Même équipe" m={d.measures.combo} combo />
        </tbody>
      </table>
      <div className="denial-splits">
        {d.splits.map((s) => (
          <div key={s.label}>
            <strong>{s.label}</strong> <small className="muted">({s.available} parties)</small>
            {s.available < 10 ? (
              <p className="muted">Échantillon trop petit.</p>
            ) : (
              <ul className="plain">
                {MEASURES.map((m) => {
                  const x = s.measures[m.key]
                  const v = verdict(x)
                  return (
                    <li key={m.key}>
                      <span>{m.label}</span>
                      <span>
                        {pct(x.rate)} <small className="muted">vs {pct(x.expected_rate)}</small>{' '}
                        <span className={v.cls}>{x.lift != null ? formatLift(x.lift) : ''}</span>
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export function DenialCard({ a, b }: { a: string; b: string }) {
  const { filters } = useScope()
  const { data, loading, error } = useAsync(() => api.denial(filters, a, b), [a, b, filters])
  return (
    <section className="card denial">
      <h3>La synergie est-elle cassée par les bans et les deny picks ?</h3>
      <p className="muted">
        Pour chaque sens, on regarde ce que devient le partenaire quand l'autre est pick, et on compare à ce qui arrive à ce même
        champion avec n'importe quel autre champion pick <strong>au même patch</strong>, au même moment de la draft et dans le même type de
        game (un champion fort, ou fort sur ce patch, est banni de toute façon).
        « Attendu » = ce niveau de référence ; « Écart » &gt; ×1,3 = les équipes le retirent nettement plus que d'habitude.
        Une corrélation, pas une preuve : un ban peut avoir d'autres raisons.
      </p>
      {error && <p className="error">{error}</p>}
      {!data && loading && <p className="muted">Calcul…</p>}
      {data && (
        <div className={loading ? 'stale' : ''}>
          <NeutralisationBlock data={data.neutralisation} />
          <h4 className="denial-subtitle">Quand l'un des deux est pick, que devient l'autre ?</h4>
          <div className="denial-grid">
            <Direction d={data.a_to_b} />
            <Direction d={data.b_to_a} />
          </div>
        </div>
      )}
    </section>
  )
}

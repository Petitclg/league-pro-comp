import { api, ROLE_LABEL, type ChampionDetail, type Role, type Spot } from './api'
import { Champ } from './Champ'
import { href, navigate } from './router'
import { useScope } from './scope'
import { SeriesList } from './SeriesView'
import { pct, rowLink, useAsync } from './utils'
import { Card, Stat, TierBadge, WrBar } from './Widgets'

const LEVEL_LABEL = { strong: 'très fiable', likely: 'fiable', hint: 'indice' } as const

function Matchups({ detail }: { detail: ChampionDetail }) {
  const byRole = new Map<Role, ChampionDetail['matchups']>()
  for (const m of detail.matchups) byRole.set(m.role, [...(byRole.get(m.role) ?? []), m])
  const roles = [...byRole.keys()]
  if (!roles.length) return <p className="muted">Pas assez de face-à-face.</p>
  const row = (m: ChampionDetail['matchups'][number]) => (
    <li key={m.champion} className="clickable" onClick={(e) => !(e.target as HTMLElement).closest('a') && navigate('duo', 'counter', detail.champion, m.champion)}>
      <Champ name={m.champion} size={26} showName />
      <a className="duo-link" href={href('duo', 'counter', detail.champion, m.champion)}>
        <WrBar wr={m.winrate} games={m.games} />
        <small className="muted"> {m.wins}-{m.games - m.wins}</small>
        {m.level && <small className="muted matchup-level">{LEVEL_LABEL[m.level]}</small>}
      </a>
    </li>
  )
  return (
    <>
      <p className="muted">
        Winrate de {detail.champion} en lane face à chaque champion ; 50 % = équilibré. Un face-à-face n'est classé favorable ou difficile que
        si l'écart ne s'explique pas par le hasard (5 parties minimum) : chaque adversaire n'apparaît donc que dans une seule liste.
      </p>
      {roles.map((role) => {
        const list = byRole.get(role)!.filter((m) => m.games >= 3)
        const favorable = list.filter((m) => m.verdict === 'favorable').sort((a, b) => b.z - a.z || b.games - a.games).slice(0, 6)
        const difficult = list.filter((m) => m.verdict === 'difficult').sort((a, b) => a.z - b.z || b.games - a.games).slice(0, 6)
        const other = list.filter((m) => !m.verdict).sort((a, b) => b.games - a.games || a.champion.localeCompare(b.champion))
        return (
          <div key={role} className="matchup-role">
            <h4>En lane {ROLE_LABEL[role]}</h4>
            {list.length === 0 ? (
              <p className="muted">Moins de 3 parties par adversaire.</p>
            ) : (
              <>
                <div className="two-col">
                  <div>
                    <span className="good-text">Favorables</span>
                    {favorable.length ? <ul className="plain">{favorable.map(row)}</ul> : <p className="muted">Aucun face-à-face concluant.</p>}
                  </div>
                  <div>
                    <span className="bad-text">Difficiles</span>
                    {difficult.length ? <ul className="plain">{difficult.map(row)}</ul> : <p className="muted">Aucun face-à-face concluant.</p>}
                  </div>
                </div>
                {other.length > 0 && (
                  <details className="matchup-other">
                    <summary>Autres face-à-face, non concluants ({other.length})</summary>
                    <ul className="plain">{other.slice(0, 12).map(row)}</ul>
                  </details>
                )}
              </>
            )}
          </div>
        )
      })}
    </>
  )
}

function SpotList({ spots, name, hot }: { spots: Spot[]; name: string; hot: boolean }) {
  if (!spots.length) return <p className="muted">Rien de notable.</p>
  return (
    <ul className="plain spots">
      {spots.map((s) => {
        const reliability = Math.abs(s.z) >= 2.33 ? 'très fiable' : Math.abs(s.z) >= 1.64 ? 'fiable' : 'indice'
        const label = (
          <>
            <small className="muted">{s.dimension}</small> {s.link ? <Champ name={s.label} size={22} showName /> : <strong>{s.label}</strong>}
          </>
        )
        const go = s.link ? () => navigate('duo', s.link!.kind, name, s.link!.other) : undefined
        return (
          <li key={`${s.dimension}${s.label}`} className={go ? 'clickable' : ''} onClick={(e) => go && !(e.target as HTMLElement).closest('a') && go()}>
            <span className="spot-label">{label}</span>
            <span className="spot-values">
              <span className={hot ? 'good-text' : 'bad-text'}>
                {pct(s.winrate)} <small>({s.delta > 0 ? '+' : ''}{Math.round(s.delta * 100)} pts)</small>
              </span>
              <small className="muted" title={`z = ${s.z} ; ${s.wins}/${s.games} victoires contre ${pct(s.baseline)} en moyenne`}>
                {s.games} parties · {pct(s.baseline)} d'habitude · {reliability}
              </small>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function ChampionPage({ name }: { name: string }) {
  const { filters } = useScope()
  const { data: d, loading, error } = useAsync(() => api.champion(name, filters), [name, filters])

  if (error) return <p className="error">Aucune donnée pour {name} avec ces filtres. ({error})</p>
  if (!d) return <p className="muted">{loading ? 'Chargement…' : ''}</p>
  const s = d.stats
  const power = d.strength
  const maxPatch = Math.max(1, ...d.by_patch.map((p) => p.pick_rate ?? 0))
  return (
    <section>
      <div className="champ-head">
        <Champ name={name} size={84} link={false} />
        <div>
          <h2>{name}</h2>
          <div className="muted">
            {d.roles.map((r) => `${ROLE_LABEL[r.role]} (${r.games})`).join(' · ') || 'Jamais pick sur cette période'}
          </div>
        </div>
        <div className="stat-row">
          <Stat label="Force" value={<TierBadge strength={power} />} hint="60 % présence (picks + bans), 40 % winrate ajusté" />
          <Stat label="Présence" value={pct(d.presence)} hint="Picks + bans / parties" />
          <Stat label="Pick rate" value={pct(d.pick_rate)} />
          <Stat label="Ban rate" value={pct(d.ban_rate)} />
          <Stat label="Winrate" value={<WrBar wr={d.winrate} />} />
          <Stat
            label={power.significant_edge ? 'WR ajusté · avantage réel' : 'WR ajusté (IC 95 %)'}
            value={`${pct(power.wr_adjusted)} ${power.significant_edge ? '✓' : ''}`}
            hint={`Winrate ramené vers 50 % selon la taille de l'échantillon. Intervalle à 95 % : ${pct(power.wr_low)} – ${pct(power.wr_high)}`}
          />
          <Stat label="Parties" value={`${d.picks} / ${d.games_in_scope}`} hint="Picks / parties de la période" />
        </div>
      </div>

      <div className="grid">
        <Card title="Spots où il est fort / faible" className="spots-card">
          {d.spots.baseline == null ? (
            <p className="muted">Pas assez de parties (16 minimum) pour comparer des contextes.</p>
          ) : (
            <>
              <p className="muted">
                Contextes (side, game de la série, moment de la draft, rôle, format, patch, équipe, joueur) où son winrate s'écarte de
                <strong> sa propre moyenne</strong> ({pct(d.spots.baseline)}), pas de 50 % : « Sous-performe » veut dire moins bien que d'habitude,
                pas forcément perdant. Les face-à-face et les partenaires ont leurs propres cartes. Plus l'échantillon est grand, plus l'écart
                est fiable ; quelques faux positifs sont normaux vu le nombre de contextes testés.
              </p>
              <div className="two-col">
                <div>
                  <h4 className="good-text">Surperforme</h4>
                  <SpotList spots={d.spots.hot} name={name} hot />
                </div>
                <div>
                  <h4 className="bad-text">Sous-performe</h4>
                  <SpotList spots={d.spots.cold} name={name} hot={false} />
                </div>
              </div>
            </>
          )}
        </Card>

        <Card title="Performance moyenne">
          <div className="stat-row small">
            <Stat label="KDA" value={s.kda ?? '–'} />
            <Stat label="K / D / A" value={s.kills != null ? `${s.kills} / ${s.deaths} / ${s.assists}` : '–'} />
            <Stat label="CS/min" value={s.cs_min ?? '–'} />
            <Stat label="Or/min" value={s.gpm ?? '–'} />
            <Stat label="Dégâts/min" value={s.dpm ?? '–'} />
            <Stat label="% dégâts équipe" value={pct(s.damage_share)} />
            <Stat label="Participation kills" value={pct(s.kill_participation)} />
            <Stat label="Durée moy." value={d.avg_duration ? `${d.avg_duration} min` : '–'} />
          </div>
        </Card>

        <Card title="Dans la draft">
          <div className="stat-row small">
            <Stat label="Pick moyen" value={d.avg_pick_slot ? `n°${d.avg_pick_slot}` : '–'} hint="Position moyenne sur les 10 picks (1 = first pick)" />
            <Stat label="Picks en 1ʳᵉ rotation" value={pct(d.first_rotation_rate)} hint="Part des picks parmi les 3 premiers de la draft" />
            <Stat label="Bans en phase 1" value={pct(d.ban_phase1_rate)} hint="Part des bans parmi les 6 premiers" />
            <Stat label="Ordre de ban moy." value={d.avg_ban_order ? `n°${d.avg_ban_order}` : '–'} />
          </div>
          <div className="two-col">
            <div>
              <h4>Selon le side</h4>
              {(['blue', 'red'] as const).map((side) => (
                <div key={side} className="line">
                  <span className={`side-dot ${side}`}>{side === 'blue' ? 'Blue' : 'Red'}</span>
                  <WrBar wr={d.sides[side]?.winrate ?? null} games={d.sides[side]?.games ?? 0} />
                </div>
              ))}
            </div>
            <div>
              <h4>Selon la game de la série</h4>
              {d.by_game_number.map((g) => (
                <div key={g.game_number} className="line">
                  <span>Game {g.game_number}</span>
                  <WrBar wr={g.winrate} games={g.games} />
                </div>
              ))}
            </div>
          </div>
        </Card>

        <Card title="Synergies (même équipe)">
          {d.partners.length === 0 && <p className="muted">Pas de paire récurrente.</p>}
          <table className="compact">
            <thead>
              <tr>
                <th>Partenaire</th>
                <th>Parties</th>
                <th>WR</th>
                <th title="Fréquence réelle / fréquence attendue au hasard">Lift</th>
              </tr>
            </thead>
            <tbody>
              {d.partners.slice(0, 10).map((p) => (
                <tr key={`${p.role}${p.champion}`} {...rowLink(() => navigate('duo', 'synergy', name, p.champion))}>
                  <td>
                    <Champ name={p.champion} size={26} showName /> <small className="muted">{ROLE_LABEL[p.role]}</small>
                  </td>
                  <td>
                    <a className="count-link" href={href('duo', 'synergy', name, p.champion)}>
                      {p.games} →
                    </a>
                  </td>
                  <td>
                    <WrBar wr={p.winrate} />
                  </td>
                  <td>{p.lift}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Counters et matchups">
          <Matchups detail={d} />
        </Card>

        <Card title="Évolution par patch">
          <table className="compact">
            <thead>
              <tr>
                <th>Patch</th>
                <th>Pick %</th>
                <th>Ban %</th>
                <th>WR</th>
              </tr>
            </thead>
            <tbody>
              {d.by_patch.slice(-12).map((p) => (
                <tr key={p.patch}>
                  <td>{p.patch}</td>
                  <td>
                    <span className="mini-bar" style={{ width: `${((p.pick_rate ?? 0) / maxPatch) * 60}px` }} /> {pct(p.pick_rate)}
                  </td>
                  <td>{pct(p.ban_rate)}</td>
                  <td>
                    <WrBar wr={p.winrate} games={p.games} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Joueurs et équipes">
          <h4>Joueurs</h4>
          <ul className="plain">
            {d.players.slice(0, 6).map((p) => (
              <li key={p.player} className="line">
                <strong>{p.player}</strong>
                <span className="muted">KDA {p.kda ?? '–'}</span>
                <WrBar wr={p.winrate} games={p.games} />
              </li>
            ))}
          </ul>
          {d.banned_by.length > 0 && (
            <>
              <h4>Souvent banni par</h4>
              <p>{d.banned_by.map((b) => `${b.team} (${b.bans})`).join(' · ')}</p>
              <h4>Souvent banni contre</h4>
              <p>{d.banned_against.map((b) => `${b.team} (${b.bans})`).join(' · ')}</p>
            </>
          )}
        </Card>
      </div>

      <h3>Parties avec {name}</h3>
      <SeriesList query={{ champion: name }} focus={[name]} pageSize={6} />
    </section>
  )
}

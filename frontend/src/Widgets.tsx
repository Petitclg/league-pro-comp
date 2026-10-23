import type { ReactNode } from 'react'
import type { ComboOpen, PairRow, Strength } from './api'
import { pct } from './utils'

/** What became of a duo each time it was open: played, taken by the opponent, banned, or left alone. */
export function comboBreakdown(o: Pick<ComboOpen, 'open' | 'realised' | 'taken' | 'banned' | 'unused'>): string {
  return (
    `Ouverte ${o.open} fois (un des deux picks, l'autre encore disponible et son poste libre) :\n` +
    `  jouée : ${o.realised}\n  prise par l'adversaire ensuite : ${o.taken}\n  bannie ensuite : ${o.banned}\n  laissée de côté : ${o.unused}`
  )
}

/** How often a duo is played when it can be: 100 % means teams always take it when it is open. */
export function ComboRate({ open }: { open: Pick<ComboOpen, 'open' | 'realised' | 'taken' | 'banned' | 'unused' | 'rate'> }) {
  if (!open.open) return <span className="muted">–</span>
  return (
    <span title={comboBreakdown(open)}>
      <strong>{pct(open.rate)}</strong> <small className="muted">{open.realised}/{open.open}</small>
    </span>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <span className="stat-value">{value ?? '–'}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}

/** Presence of a duo: played together + bans of a member beyond chance, over the games of the scope. */
export function PairPresence({ row, max }: { row: PairRow; max: number }) {
  const { presence, presence_played: played, presence_countered: countered, bans } = row
  if (presence == null) return <span className="muted">–</span>
  const scale = max > 0 ? 100 / max : 0
  const title =
    `Jouée ensemble : ${row.games} parties (${pct(played, 1)})\n` +
    `Un des deux banni dans ${bans.either} parties, contre ${bans.expected} attendues si les bans étaient indépendants (même patch) :\n` +
    (row.countered > 0
      ? `  → +${row.countered} parties comptées (${pct(countered, 1)})\n`
      : `  → rien de plus que le hasard, non compté\n`) +
    (row.realised_share != null ? `Part jouée de la présence : ${pct(row.realised_share)}` : '')
  return (
    <span className="presence" title={title}>
      <strong>{pct(presence, 1)}</strong>
      <span className="presence-track">
        <span className="presence-played" style={{ width: `${(played ?? 0) * scale}%` }} />
        <span className="presence-countered" style={{ width: `${(countered ?? 0) * scale}%` }} />
      </span>
      {row.countered > 0 && <small className="muted">{pct(row.realised_share)} jouée</small>}
    </span>
  )
}

/** Winrate bar centered on 50%. */
export function WrBar({ wr, games }: { wr: number | null; games?: number }) {
  if (wr == null) return <span className="muted">–</span>
  const good = wr >= 0.5
  return (
    <span className="wr-bar" title={games != null ? `${games} parties` : undefined}>
      <span className="wr-track">
        <span
          className={`wr-fill ${good ? 'good' : 'bad'}`}
          style={{ width: `${Math.abs(wr - 0.5) * 100}%`, [good ? 'left' : 'right']: '50%' }}
        />
      </span>
      <span className={good ? 'good-text' : 'bad-text'}>{pct(wr)}</span>
      {games != null && <span className="muted"> ({games})</span>}
    </span>
  )
}

export function TierBadge({ strength }: { strength: Strength }) {
  if (!strength.tier) return <span className="muted">–</span>
  return (
    <span
      className={`tier tier-${strength.tier}`}
      title={`Indice de force ${strength.score}/100 : 60 % présence (picks + bans), 40 % winrate ajusté`}
    >
      {strength.tier} <small>{strength.score}</small>
    </span>
  )
}

export function Card({ title, children, className = '' }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

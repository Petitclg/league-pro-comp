import { useState } from 'react'
import { api, ROLES, ROLE_LABEL } from './api'
import { Champ } from './Champ'
import { ChampionSelect } from './ChampionSelect'
import { href, navigate } from './router'
import { useScope } from './scope'
import { pct, rowLink, useAsync } from './utils'
import { ComboRate, PairPresence, WrBar } from './Widgets'

export function PairsPage() {
  const { filters } = useScope()
  const [minGames, setMinGames] = useState(5)
  const [minLiftText, setMinLiftText] = useState('')
  const minLift = Math.max(0, Number(minLiftText.replace(',', '.')) || 0)
  const [roleA, setRoleA] = useState('')
  const [roleB, setRoleB] = useState('')
  const [champion, setChampion] = useState('')
  const [sort, setSort] = useState('games')
  const { data, loading, error } = useAsync(
    () =>
      api.pairs(filters, {
        min_games: minGames,
        min_lift: minLift || undefined,
        role_a: roleA,
        role_b: roleB,
        champion,
        sort,
        limit: 100,
      }),
    [filters, minGames, minLift, roleA, roleB, champion, sort],
  )
  const maxPresence = Math.max(0, ...(data?.pairs ?? []).map((p) => p.presence ?? 0))
  const roleSelect = (value: string, set: (v: string) => void, label: string) => (
    <select value={value} onChange={(e) => set(e.target.value)} aria-label={label}>
      <option value="">{label}</option>
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {ROLE_LABEL[r]}
        </option>
      ))}
    </select>
  )
  return (
    <section>
      <div className="filters">
        <ChampionSelect value={champion} onChange={setChampion} placeholder="Paires contenant…" />
        {roleSelect(roleA, setRoleA, 'Rôle A')}
        {roleSelect(roleB, setRoleB, 'Rôle B')}
        <label>
          Min parties
          <input type="number" min={1} value={minGames} onChange={(e) => setMinGames(Math.max(1, Number(e.target.value) || 1))} />
        </label>
        <label title="Ne garde que les paires jouées ensemble au moins ce nombre de fois plus que prévu par le hasard (1 = pas de lien, 2 = deux fois plus)">
          Lift min
          <input
            type="number"
            min={0}
            step={0.5}
            placeholder="0"
            value={minLiftText}
            onChange={(e) => setMinLiftText(e.target.value)}
          />
        </label>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Tri">
          <option value="games">Tri : fréquence</option>
          <option value="presence">Tri : présence (jouée + bans)</option>
          <option value="realised">Tri : jouée quand elle est ouverte</option>
          <option value="lift">Tri : synergie (lift)</option>
          <option value="winrate">Tri : winrate</option>
        </select>
      </div>
      <p className="muted">
        Le <em>lift</em> compare la fréquence réelle de la paire à celle attendue au hasard (&gt; 1 = picks ensemble plus que prévu).
        Cliquez sur une ligne pour voir les statistiques de la paire et les drafts concernées ; cliquez sur un champion pour sa page.
        La <em>présence</em> de la paire, comme celle d'un champion, ajoute aux parties où elle est <strong>jouée</strong> (barre dorée) celles
        où les adversaires la <strong>cassent par un ban</strong> (barre rouge) : un des deux champions est banni plus souvent que si les
        bans étaient indépendants, au même patch. Deux champions forts qui sont simplement bannis ensemble ne comptent pas. Détail au survol.
        La colonne <em>Jouée si ouverte</em> répond à une autre question : quand une équipe prend un des deux champions alors que l'autre est
        encore disponible (ni banni, ni pris par l'adversaire, poste libre), combien de fois prend-elle aussi le second ? 100 % = la paire est
        systématiquement jouée dès qu'elle peut l'être ; le détail (prise par l'adversaire, bannie ensuite, laissée de côté) est au survol.
        Ces deux chiffres alimentent aussi les suggestions du tracker.
      </p>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Chargement…</p>}
      {data && (
        <table>
          <thead>
            <tr>
              <th>Paire</th>
              <th>Parties</th>
              <th>Pick rate</th>
              <th title="Parties où la paire est jouée ou contrée par les adversaires, sur l'ensemble des parties">Présence</th>
              <th title="Quand une équipe prend un des deux champions alors que l'autre est encore disponible, part des fois où elle prend les deux">Jouée si ouverte</th>
              <th>Winrate</th>
              <th>Lift</th>
            </tr>
          </thead>
          <tbody>
            {data.pairs.map((p) => (
              <tr key={`${p.a.role}${p.a.champion}${p.b.role}${p.b.champion}`} {...rowLink(() => navigate('duo', 'synergy', p.a.champion, p.b.champion))}>
                <td className="pair">
                  <Champ name={p.a.champion} size={32} showName />
                  <small>{ROLE_LABEL[p.a.role]}</small>
                  <span>+</span>
                  <Champ name={p.b.champion} size={32} showName />
                  <small>{ROLE_LABEL[p.b.role]}</small>
                </td>
                <td>
                  <a className="count-link" href={href('duo', 'synergy', p.a.champion, p.b.champion)}>
                    {p.games} parties →
                  </a>
                </td>
                <td>{pct(p.pick_rate, 1)}</td>
                <td>
                  <PairPresence row={p} max={maxPresence} />
                </td>
                <td>
                  <ComboRate open={p.open} />
                </td>
                <td>
                  <WrBar wr={p.winrate} />
                </td>
                <td>{p.lift.toFixed(2)}</td>
              </tr>
            ))}
            {data.pairs.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  Aucune paire avec ces filtres.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  )
}

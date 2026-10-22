import { useState } from 'react'
import { api, type CounteredItem, type CounteredList, type CounteredRanking, type RankingLevel } from './api'
import { Champ } from './Champ'
import { navigate } from './router'
import { useScope } from './scope'
import { formatLift, pct, useAsync } from './utils'

const LEVEL_LABEL: Record<RankingLevel, string> = {
  strong: 'Très fiable',
  likely: 'Probable',
  hint: 'Simple indice',
}

function Item({ item, verb }: { item: CounteredItem; verb: (i: CounteredItem) => string }) {
  const splits = item.splits.filter((s) => s.available >= 15 && s.lift != null)
  return (
    <li className="counter-item clickable" onClick={(e) => !(e.target as HTMLElement).closest('a') && navigate('duo', 'synergy', item.trigger, item.partner)}>
      <div className="counter-head">
        <Champ name={item.trigger} size={32} showName />
        <span className="arrow">→</span>
        <Champ name={item.partner} size={32} showName />
        <span className={`level level-${item.level}`}>{LEVEL_LABEL[item.level]}</span>
      </div>
      <p>
        Quand <strong>{item.trigger}</strong> est pick, <strong>{item.partner}</strong> {verb(item)} dans <strong>{pct(item.rate)}</strong> des
        parties ({item.count} sur {item.available}), contre {pct(item.expected_rate)} d'habitude :{' '}
        <strong className="bad-text">{formatLift(item.lift)}</strong>.
      </p>
      {splits.length > 0 && (
        <p className="muted counter-splits">
          {splits.map((s) => `${s.label} : ${formatLift(s.lift)} (${pct(s.rate)} contre ${pct(s.expected_rate)})`).join(' · ')}
        </p>
      )}
    </li>
  )
}

function NeutralItem({ item }: { item: CounteredRanking['neutralised']['items'][number] }) {
  const [g1, g2, total] = item.groups
  const excess = Math.round(total.expected_both_banned - total.both_banned)
  return (
    <li className="counter-item clickable" onClick={(e) => !(e.target as HTMLElement).closest('a') && navigate('duo', 'synergy', item.a, item.b)}>
      <div className="counter-head">
        <Champ name={item.a} size={32} showName />
        <span className="arrow">ou</span>
        <Champ name={item.b} size={32} showName />
        <span className={`level level-${item.level}`}>{LEVEL_LABEL[item.level]}</span>
      </div>
      <p>
        <strong>{item.a}</strong> ou <strong>{item.b}</strong> est banni dans <strong>{pct(total.either_rate)}</strong> des parties (
        {total.either_banned} sur {total.n}), contre {pct(total.expected_either_rate)} si les bans étaient indépendants. Ils sont bannis tous les
        deux {total.both_banned} fois seulement, au lieu de {Math.round(total.expected_both_banned)} : les équipes en bannissent{' '}
        <strong>un OU l'autre</strong> ({excess} parties de moins que prévu).
      </p>
      <p className="muted counter-splits">
        {[g1, g2]
          .filter((g) => g.n >= 20)
          .map((g) => `${g.label} : ${pct(g.either_rate)} contre ${pct(g.expected_either_rate)} (${g.n} parties)`)
          .join(' · ')}
      </p>
    </li>
  )
}

function LevelCounts({ found, chance }: { found: Record<RankingLevel, number>; chance: Record<RankingLevel, number> }) {
  return (
    <p className="muted level-counts">
      {(['strong', 'likely', 'hint'] as RankingLevel[]).map((k) => (
        <span key={k}>
          <span className={`level level-${k}`}>{LEVEL_LABEL[k]}</span> {found[k]} <small>(hasard ≈ {chance[k]})</small>
        </span>
      ))}
    </p>
  )
}

function Section({
  title,
  help,
  list,
  verb,
  showHints,
}: {
  title: string
  help: string
  list: CounteredList
  verb: (i: CounteredItem) => string
  showHints: boolean
}) {
  const items = list.items.filter((i) => showHints || i.level !== 'hint')
  return (
    <section className="card">
      <h3>{title}</h3>
      <p className="muted">{help}</p>
      {items.length === 0 ? (
        <p className="muted">Aucun cas assez net sur cette période.</p>
      ) : (
        <ul className="plain counter-list">
          {items.map((i) => (
            <Item key={`${i.trigger}${i.partner}`} item={i} verb={verb} />
          ))}
        </ul>
      )}
    </section>
  )
}

function Summary({ data }: { data: CounteredRanking }) {
  return (
    <table className="compact summary-table">
      <thead>
        <tr>
          <th>Fiabilité</th>
          <th>Cas trouvés (anticipation + réaction)</th>
          <th title="Nombre de cas que le seul hasard produirait parmi les synergies testées, pour chacune des deux mesures">
            Dont dus au hasard (à peu près)
          </th>
        </tr>
      </thead>
      <tbody>
        {data.levels.map((lv) => (
          <tr key={lv.key}>
            <td>
              <span className={`level level-${lv.key}`}>{LEVEL_LABEL[lv.key]}</span>
            </td>
            <td>{data.anticipation.by_level[lv.key] + data.reaction.by_level[lv.key]}</td>
            <td className="muted">{(lv.by_chance * 2).toFixed(1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function CounteredPage() {
  const { filters } = useScope()
  const { data, loading, error } = useAsync(() => api.countered(filters), [filters])
  const [hintsChoice, setHintsChoice] = useState<boolean | null>(null)

  const showHints = hintsChoice ?? false

  return (
    <section>
      <details className="howto" open>
        <summary>Comment lire cette page</summary>
        <p>
          Les joueurs pro savent casser une synergie. Il y a trois façons, trois sections ci-dessous :
        </p>
        <ul>
          <li>
            <strong>Bannir l'un ou l'autre</strong> avant les picks (ex. Nocturne <em>ou</em> Orianna banni en game 1) : le duo n'existe jamais.
            On compare à ce que donneraient des bans indépendants, patch par patch.
          </li>
          <li>
            <strong>Anticipation</strong> : Aphelios est déjà banni quand Lucian est pick.
          </li>
          <li>
            <strong>Réaction</strong> : après le pick de Lucian, l'adversaire pick ou bannit Aphelios.
          </li>
        </ul>
        <ul>
          <li>
            <strong>Observé</strong> : dans quelle part des parties où Lucian est pick, Aphelios est retiré (pické en face ou banni).
          </li>
          <li>
            <strong>« D'habitude »</strong> : ce qui arrive à Aphelios quand un <em>autre</em> champion est pick, au même patch, au même
            moment de la draft et dans le même type de game. Ça évite de compter comme « contre » un champion simplement très banni.
          </li>
          <li>
            <strong>×2</strong> = deux fois plus souvent que d'habitude. <strong>Game 1</strong> : tous les champions sont disponibles ;
            <strong> Games 2+</strong> : en Fearless, une partie des champions est déjà verrouillée.
          </li>
          <li>
            <strong>Fiabilité</strong> : sur peu de parties, un écart peut venir du hasard. « Très fiable » ≈ plus d'1 chance sur 2 000 d'être
            un hasard ; « probable » ≈ 1 sur 100 ; « simple indice » ≈ 1 sur 20, à prendre avec prudence.
          </li>
        </ul>
        <p className="muted">
          Un ban peut avoir d'autres raisons qu'une synergie : c'est une corrélation, pas une preuve. Avec une seule année, il y a peu de parties,
          donc peu de cas ressortent nettement : élargissez la période (filtre « Toutes les périodes » ou « Fearless uniquement ») pour en voir plus.
        </p>
      </details>

      {error && <p className="error">{error}</p>}
      {!data && loading && <p className="muted">Calcul en cours (quelques secondes la première fois)…</p>}
      {data && (
        <div className={loading ? 'results stale' : 'results'}>
          <h3>Bilan sur la période sélectionnée</h3>
          <p className="muted">
            {data.tested} synergies testées (paires jouées ensemble nettement plus que le hasard, dans chaque sens). Un cas n'est retenu que
            si le partenaire est retiré au moins ×{data.lift_min} plus souvent que d'habitude.
          </p>
          <Summary data={data} />
          {data.levels.some(
            (lv) => data.anticipation.by_level[lv.key] + data.reaction.by_level[lv.key] <= lv.by_chance * 2 && lv.key !== 'strong',
          ) && (
            <p className="muted">
              ⚠ Quand le nombre de cas trouvés est proche de la colonne « hasard », ce niveau ne prouve rien : le hasard seul en produirait
              autant. Sur une période courte, seuls les cas « très fiables » sont vraiment crédibles.
            </p>
          )}
          <label className="switch hints-toggle">
            <input type="checkbox" checked={showHints} onChange={(e) => setHintsChoice(e.target.checked)} />
            Afficher aussi les simples indices (moins fiables)
          </label>

          <div className="grid">
            <section className="card wide">
              <h3>Le duo est neutralisé par un ban : l'un ou l'autre est banni</h3>
              <p className="muted">
                Les synergies connues (jouées ensemble nettement plus que le hasard sur toute l'histoire) pour lesquelles les équipes bannissent
                un des deux, rarement les deux. C'est ce qui se passe <em>avant</em> les picks : un duo banni n'est jamais joué, donc il
                n'apparaît pas dans les deux listes suivantes.
              </p>
              <LevelCounts found={data.neutralised.by_level} chance={data.neutralised.chance} />
              {(() => {
                const items = data.neutralised.items.filter((i) => showHints || i.level !== 'hint')
                return items.length === 0 ? (
                  <p className="muted">Aucun cas assez net sur cette période.</p>
                ) : (
                  <ul className="plain counter-list neutral-list">
                    {items.map((i) => (
                      <NeutralItem key={`${i.a}${i.b}`} item={i} />
                    ))}
                  </ul>
                )
              })()}
            </section>
          </div>

          <div className="grid">
            <Section
              title="Le partenaire est déjà banni avant le pick (anticipation)"
              help="L'équipe adverse a banni le partenaire avant même que le premier champion soit pick."
              list={data.anticipation}
              showHints={showHints}
              verb={() => 'est déjà banni avant'}
            />
            <Section
              title="Le partenaire est pris ou banni après le pick (réaction)"
              help="Une fois le premier champion pick, l'adversaire pick ou bannit le partenaire."
              list={data.reaction}
              showHints={showHints}
              verb={() => 'est pické en face ou banni ensuite'}
            />
          </div>
        </div>
      )}
    </section>
  )
}

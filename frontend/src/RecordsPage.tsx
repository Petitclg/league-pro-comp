import { api, type RecordItem } from './api'
import { Champ } from './Champ'
import { href } from './router'
import { useScope } from './scope'
import { useAsync } from './utils'

function Item({ item }: { item: RecordItem }) {
  const target = item.link && item.champions.length === 2
    ? href('duo', item.link, item.champions[0], item.champions[1])
    : item.game_id
      ? href('game', item.game_id)
      : null
  const body = (
    <>
      <span className="rec-champs">
        {item.champions.map((c) => (
          <Champ key={c} name={c} size={30} link={!target} />
        ))}
      </span>
      <span className="rec-label">
        <strong>{item.label}</strong>
        <small className="muted">{item.sub}</small>
      </span>
      <span className="rec-value">{item.value}</span>
    </>
  )
  return target ? (
    <a className="rec-item" href={target}>
      {body}
    </a>
  ) : (
    <div className="rec-item">{body}</div>
  )
}

export function RecordsPage() {
  const { filters } = useScope()
  const { data, loading, error } = useAsync(() => api.records(filters), [filters])
  return (
    <section>
      <p className="muted">Statistiques marquantes de la période sélectionnée. Cliquez sur un élément pour voir les parties.</p>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Calcul…</p>}
      <div className="grid">
        {data?.map((s) => (
          <section key={s.id} className="card">
            <h3>{s.title}</h3>
            {s.description && <p className="muted">{s.description}</p>}
            {s.items.map((it, i) => (
              <Item key={i} item={it} />
            ))}
          </section>
        ))}
      </div>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { api, ROLE_LABEL, ROLES, type ImportResult, type Role } from './api'
import { Champ } from './Champ'
import { useDDragon } from './ddragon'
import { pct, useAsync } from './utils'

/** The pros' items, runes and spells for one of the locked champions, from games like the current draft. */
export function BuildPanel({
  request,
  locked,
  myRole,
  autoImport,
}: {
  request: string
  locked: Partial<Record<Role, string>>
  myRole?: Role | null
  autoImport: boolean
}) {
  const dd = useDDragon()
  const [chosen, setChosen] = useState<Role | null>(null)
  const [status, setStatus] = useState<ImportResult | null>(null)
  const roles = ROLES.filter((r) => locked[r])
  const role = (chosen && locked[chosen] ? chosen : myRole && locked[myRole] ? myRole : roles[0]) as Role | undefined
  const champion = role ? locked[role] : undefined
  const body = JSON.stringify({ ...JSON.parse(request), champion, role })
  const { data, loading, error } = useAsync(
    () => (champion ? api.build(JSON.parse(body)) : Promise.resolve(null)),
    [body],
  )

  useEffect(() => {
    if (!autoImport || !data || data.games === 0 || role !== myRole) return
    api.importBuild(JSON.parse(body)).then(setStatus, (e) => setStatus({ sent: false, reason: String(e) }))
    // The server skips a build identical to the last one written for this champion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoImport, data, role, myRole])

  if (!role || !champion) return null
  const icon = (id: number, name: string) =>
    dd?.version ? (
      <img
        src={`https://ddragon.leagueoflegends.com/cdn/${dd.version}/img/item/${id}.png`}
        width={32}
        height={32}
        alt={name}
        title={name}
        loading="lazy"
      />
    ) : (
      <span className="champ-fallback" style={{ width: 32, height: 32 }}>
        {name.slice(0, 2)}
      </span>
    )

  return (
    <div className={loading ? 'build-panel stale' : 'build-panel'}>
      <div className="build-head">
        <h3>Build des pros</h3>
        <Champ name={champion} size={28} link={false} />
        <select value={role} onChange={(e) => setChosen(e.target.value as Role)} aria-label="Champion du build">
          {roles.map((r) => (
            <option key={r} value={r}>
              {locked[r]} ({ROLE_LABEL[r]})
            </option>
          ))}
        </select>
        <button
          disabled={!data || data.games === 0}
          onClick={() => api.importBuild(JSON.parse(body)).then(setStatus, (e) => setStatus({ sent: false, reason: String(e) }))}
          title="Écrit un set d'items et une page de runes dans le client LoL (préfixe [Pro])"
        >
          Importer dans le client
        </button>
        {status && (
          <span className={status.sent ? 'good-text' : 'muted'}>
            {status.sent
              ? `Importé${status.items === 'ok' ? ' : items' : ''}${status.runes === 'ok' ? ' + runes' : status.runes ? ` (runes : ${status.runes})` : ''}`
              : (status.reason ?? '')}
          </span>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {data && data.games === 0 && (
        <p className="muted">
          Aucune partie pro avec {champion} ({ROLE_LABEL[role]}) et ses items dans ce périmètre. Les parties déjà importées n'ont pas
          d'items : relancez l'import pour les récupérer.
        </p>
      )}
      {data && data.games > 0 && (
        <>
          <p className="muted">
            {data.games} parties pro dont {data.similar_games} avec des alliés ou adversaires en commun (pondérées, patchs récents favorisés).
            L'ordre d'achat n'est pas disponible : les items sont classés par fréquence.
          </p>
          {data.item_blocks.length === 0 && <p className="muted">Données d'items indisponibles (Data Dragon injoignable ?).</p>}
          <div className="build-blocks">
            {data.item_blocks.map((b) => (
              <div key={b.type} className="build-block">
                <strong>{b.type}</strong>
                <div className="build-items">
                  {b.items.map((i) => (
                    <span key={i.id} className="build-item" title={`${i.name} : ${pct(i.rate)} des parties`}>
                      {icon(i.id, i.name)}
                      <small>{pct(i.rate)}</small>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="build-runes">
            {data.runes.map((p) => (
              <div key={p.keystone} className="build-block">
                <strong>
                  {p.keystone} · {p.primary_tree} / {p.secondary_tree}
                </strong>{' '}
                <small className="muted">{pct(p.share)} des parties</small>
                <div className="muted">{p.runes.slice(1).join(' · ')}</div>
              </div>
            ))}
            {data.spells.length > 0 && (
              <div className="build-block">
                <strong>Sorts</strong> <span>{data.spells.join(' + ')}</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

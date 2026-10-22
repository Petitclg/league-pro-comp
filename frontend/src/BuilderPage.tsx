import { useEffect, useRef, useState } from 'react'
import { api, ROLES, ROLE_LABEL, selectedLeagues, type LiveState, type Role } from './api'
import { Champ } from './Champ'
import { ChampionSelect } from './ChampionSelect'
import { useScope } from './scope'
import { pct, useAsync, useDebounced } from './utils'

const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function ChampionList({
  title,
  hint,
  items,
  onChange,
  exclude,
}: {
  title: string
  hint: string
  items: string[]
  onChange: (items: string[]) => void
  exclude: string[]
}) {
  return (
    <div className="champ-list-input">
      <label title={hint}>{title}</label>
      <ChampionSelect
        value=""
        onChange={(n) => n && !items.includes(n) && onChange([...items, n])}
        placeholder="Ajouter…"
        clearOnPick
        exclude={[...items, ...exclude]}
      />
      <div className="chips-inline">
        {items.map((c) => (
          <button key={c} className="chip-btn" onClick={() => onChange(items.filter((x) => x !== c))} title="Retirer">
            <Champ name={c} size={20} link={false} /> {c} ✕
          </button>
        ))}
      </div>
    </div>
  )
}

function useTracker(enabled: boolean, onState: (s: LiveState) => void) {
  const [status, setStatus] = useState<LiveState | null>(null)
  const handler = useRef(onState)
  useEffect(() => {
    handler.current = onState
  })
  useEffect(() => {
    if (!enabled) return
    let alive = true
    const tick = async () => {
      try {
        const s = await api.live()
        if (!alive) return
        setStatus(s)
        if (s.in_champ_select) handler.current(s)
      } catch (e) {
        if (alive) setStatus({ connected: false, in_champ_select: false, reason: String(e) })
      }
    }
    tick()
    const id = window.setInterval(tick, 2500)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [enabled])
  return enabled ? status : null
}

export function BuilderPage() {
  const { filters } = useScope()
  const [picks, setPicks] = useState<Partial<Record<Role, string>>>({})
  const [enemy, setEnemy] = useState<string[]>([])
  const [bans, setBans] = useState<string[]>([])
  const [fearlessLocked, setFearlessLocked] = useState<string[]>([])
  const [tracking, setTracking] = useState(false)
  const [sendToChat, setSendToChat] = useState(false)
  const [expand, setExpand] = useState(false)
  const status = useTracker(tracking, (s) => {
    // Keep the same references when nothing changed, otherwise every poll would trigger a recompute.
    setPicks((prev) => (sameJson(prev, s.my_team ?? {}) ? prev : (s.my_team ?? {})))
    setEnemy((prev) => (sameJson(prev, s.enemy_picks ?? []) ? prev : (s.enemy_picks ?? [])))
    setBans((prev) => (sameJson(prev, s.bans ?? []) ? prev : (s.bans ?? [])))
  })

  const locked = Object.fromEntries(Object.entries(picks).filter(([, c]) => c)) as Partial<Record<Role, string>>
  const unavailable = [...bans, ...fearlessLocked]
  const request = useDebounced(
    JSON.stringify({
      picks: locked,
      enemy,
      unavailable,
      expand,
      league: selectedLeagues(filters),
      team: filters.team,
      tournament: filters.tournament,
      year: filters.year,
      patch: filters.patch,
      fearless: filters.fearless,
    }),
    200,
  )
  const { data, loading, error } = useAsync(() => api.suggest(JSON.parse(request)), [request])

  const inChampSelect = !!status?.in_champ_select
  const myRole = status?.my_role
  useEffect(() => {
    if (!sendToChat || !inChampSelect || !data || loading) return
    api.sendRecos(JSON.parse(request), myRole ?? undefined).catch(() => undefined)
    // The server skips a message identical to the last one sent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendToChat, inChampSelect, data, loading, request])

  const allPicked = [...Object.values(locked), ...enemy]

  return (
    <section>
      <div className="tracker">
        <label className="switch">
          <input type="checkbox" checked={tracking} onChange={(e) => setTracking(e.target.checked)} />
          Tracker : importer automatiquement ma draft depuis le client LoL
        </label>
        <label className="switch" title="Poste une ligne de recommandations (en anglais) dans le chat de la sélection des champions">
          <input
            type="checkbox"
            checked={sendToChat}
            disabled={!tracking}
            onChange={(e) => setSendToChat(e.target.checked)}
          />
          Envoyer les recommandations dans le chat
        </label>
        <label
          className="switch"
          title="Un champion jamais joué en pro à son poste est remplacé par les champions pro qui lui ressemblent (tags et profil Data Dragon), avec un poids réduit"
        >
          <input type="checkbox" checked={expand} onChange={(e) => setExpand(e.target.checked)} />
          Mode élargi : remplacer les champions jamais joués en pro par des équivalents
        </label>
        {status && (
          <span className={status.in_champ_select ? 'good-text' : 'muted'}>
            {status.in_champ_select
              ? `Champ select détecté (${status.phase ?? '…'}) — picks et bans synchronisés`
              : (status.reason ?? 'En attente du client…')}
          </span>
        )}
      </div>
      <p className="muted">
        Renseignez les champions déjà choisis : les suggestions se basent sur les comps pro qui leur ressemblent (pondérées par le
        nombre de picks communs, victoires favorisées) et, si des picks adverses sont connus, sur les winrates en face-à-face.
        Les filtres du haut (équipe, patch, Fearless…) restreignent les comps de référence.
      </p>

      <div className="slots">
        {ROLES.map((role) => (
          <div key={role} className="slot">
            <label>{ROLE_LABEL[role]}</label>
            <div className="slot-pick">
              {locked[role] ? <Champ name={locked[role]!} size={56} /> : <div className="empty-slot" />}
              {locked[role] && !tracking && (
                <button
                  className="slot-remove"
                  onClick={() => setPicks((p) => ({ ...p, [role]: undefined }))}
                  title={`Retirer ${locked[role]}`}
                  aria-label={`Retirer ${locked[role]} (${ROLE_LABEL[role]})`}
                >
                  ✕
                </button>
              )}
            </div>
            <ChampionSelect
              value={picks[role] ?? ''}
              onChange={(n) => setPicks((p) => ({ ...p, [role]: n || undefined }))}
              placeholder="Champion"
              exclude={[...allPicked.filter((c) => c !== picks[role]), ...unavailable]}
            />
          </div>
        ))}
      </div>

      <div className="lists">
        <ChampionList title="Picks adverses" hint="Utilisés pour pondérer les counters" items={enemy} onChange={setEnemy} exclude={allPicked} />
        <ChampionList title="Bans" hint="Champions bannis" items={bans} onChange={setBans} exclude={allPicked} />
        <ChampionList
          title="🔒 Fearless : déjà joués dans la série"
          hint="En Fearless, ces champions ne peuvent plus être pick"
          items={fearlessLocked}
          onChange={setFearlessLocked}
          exclude={allPicked}
        />
      </div>

      {error && <p className="error">{error}</p>}
      <p className="muted status-line">
        {data ? `${data.matching_comps} comps pro comparables.` : ''}
        {data && data.unknown_picks.length > 0 && (
          <span className="bad-text">
            {' '}
            Jamais joués en pro à ce poste : {data.unknown_picks.map((r) => `${locked[r]} (${ROLE_LABEL[r]})`).join(', ')}
            {Object.keys(data.substitutes).length > 0
              ? ` — remplacés par ${Object.entries(data.substitutes)
                  .map(([r, cs]) => `${locked[r as Role]} → ${cs?.join(', ')}`)
                  .join(' ; ')}.`
              : ' — ignorés.'}
          </span>
        )}
        {loading && <span className="computing"> Mise à jour…</span>}
      </p>
      {data && (
        <div className={loading ? 'results stale' : 'results'}>
          <div className="suggestions">
            {ROLES.filter((r) => !locked[r]).map((role) => (
              <div key={role} className="sugg-col">
                <h3>{ROLE_LABEL[role]}</h3>
                {(data.suggestions[role] ?? []).map((s) => (
                  <button
                    key={s.champion}
                    className="sugg"
                    onClick={() => setPicks((p) => ({ ...p, [role]: s.champion }))}
                    title={s.synergies
                      .map((y) => `${y.champion} (${ROLE_LABEL[y.role]}) : ${y.games} parties, ${pct(y.winrate)} WR`)
                      .join('\n')}
                  >
                    <Champ name={s.champion} size={36} showName link={false} />
                    <small>
                      {pct(s.share)} des comps · {s.games} parties · {pct(s.winrate)} WR
                    </small>
                    {s.counter_winrate != null && (
                      <small className={s.counter_winrate >= 0.5 ? 'good-text' : 'bad-text'}>
                        vs adversaires : {pct(s.counter_winrate)} ({s.counter_games} parties)
                      </small>
                    )}
                  </button>
                ))}
                {(data.suggestions[role] ?? []).length === 0 && <p className="muted">Aucune donnée</p>}
              </div>
            ))}
          </div>

          {data.closest_teams.length > 0 && (
            <>
              <h3>Équipes les plus proches</h3>
              <ul className="teams">
                {data.closest_teams.map((t) => (
                  <li key={t.team}>
                    <strong>{t.team}</strong> — jusqu'à {t.best_match}/{Object.keys(locked).length} picks en commun ({t.games} comps)
                  </li>
                ))}
              </ul>
            </>
          )}

          <h3>Comps pro similaires</h3>
          {data.similar_comps.map((c) => (
            <div key={`${c.game_id}${c.team}`} className="similar">
              <div className="similar-head">
                <strong>{c.team}</strong> vs {c.opponent} · {c.date?.slice(0, 10)}
                {c.game_number ? ` · Game ${c.game_number}` : ''}
                <span className={`badge ${c.win ? 'win' : c.win === false ? 'loss' : ''}`}>
                  {c.win ? 'Victoire' : c.win === false ? 'Défaite' : '?'}
                </span>
                <span className="badge">{c.match} en commun</span>
                <a href={`#/game/${encodeURIComponent(c.game_id)}`}>voir la draft</a>
              </div>
              <div className="comp">
                {ROLES.map((r) => (
                  <Champ key={r} name={c.picks[r]} size={36} showName />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

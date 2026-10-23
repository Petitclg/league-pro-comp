import { ChampionSelect } from './ChampionSelect'
import { SeriesList } from './SeriesView'
import { usePersistentState } from './utils'

export function DraftsPage() {
  const [champion, setChampion] = usePersistentState('drafts.champion', '')
  const [role, setRole] = usePersistentState('drafts.role', '')
  return (
    <section>
      <div className="filters">
        <ChampionSelect value={champion} onChange={setChampion} placeholder="Filtrer par champion (pick ou ban)…" />
        {champion && (
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Rôle">
            <option value="">Tous rôles</option>
            {['top', 'jungle', 'mid', 'bot', 'support'].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        )}
      </div>
      <p className="muted legend">
        Les numéros sur les icônes indiquent l'ordre de la draft (1 = première sélection, côté Blue). 🔗 synergie · ⚔ counter de lane · ★ signature du joueur · ◆ pick rare.
      </p>
      <SeriesList query={{ champion: champion || undefined, role: champion ? role || undefined : undefined }} focus={champion ? [champion] : []} />
    </section>
  )
}

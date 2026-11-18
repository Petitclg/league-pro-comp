import { createContext, useContext } from 'react'
import type { Filters, Meta } from './api'

export interface Scope {
  meta: Meta
  filters: Filters
  setFilters: (f: Filters) => void
}

export const ScopeContext = createContext<Scope | null>(null)

export function useScope(): Scope {
  const scope = useContext(ScopeContext)
  if (!scope) throw new Error('ScopeContext missing')
  return scope
}

/** Perimeter key of the solo queue games (Riot API), offered next to the pro leagues. */
export const SOLOQ = 'SOLOQ'

export const RANK_LABEL: Record<string, string> = {
  EMERALD: 'Émeraude',
  DIAMOND: 'Diamant',
  MASTER: 'Maître',
  GRANDMASTER: 'Grand maître',
  CHALLENGER: 'Challenger',
}

/**
 * New league selection after a change in the picker. The solo queue cannot be mixed with pro
 * leagues: picking it drops the leagues, picking a league drops it.
 */
export function selectLeagues(previous: string[], next: string[]): string[] {
  const added = next.filter((k) => !previous.includes(k))
  if (added.includes(SOLOQ)) return [SOLOQ]
  if (next.includes(SOLOQ) && next.length > 1) return next.filter((k) => k !== SOLOQ)
  return next
}

/** Filters that only mean something for pro games (or only for the solo queue) are dropped when switching. */
export function filtersForScope(f: Filters, soloq: boolean): Filters {
  return soloq
    ? { ...f, team: undefined, fearless: undefined, game_number: undefined }
    : { ...f, rank: undefined }
}

/** Synergies contrées need the draft order (pro only); items and the win probability need solo queue timelines. */
export function tabVisible(id: string, soloq: boolean): boolean {
  if (id === 'countered') return !soloq
  if (id === 'items') return soloq
  if (id === 'winprob') return soloq
  return true
}

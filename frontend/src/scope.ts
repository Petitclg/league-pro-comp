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

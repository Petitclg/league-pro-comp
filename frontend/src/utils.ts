import { useEffect, useState, type MouseEvent } from 'react'

export function pct(v: number | null | undefined, digits = 0): string {
  return v == null ? '–' : `${(v * 100).toFixed(digits)}%`
}

/** Props making a whole table row open `go()`, while links inside the row keep their own target. */
export function rowLink(go: () => void) {
  return {
    className: 'clickable',
    onClick: (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('a')) go()
    },
  }
}

const UI_PREFIX = 'lolpro-ui:'

/** `useState` kept in localStorage, so a page keeps its settings when the user leaves it or restarts the app. */
export function usePersistentState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(UI_PREFIX + key)
      if (raw !== null) return JSON.parse(raw) as T
    } catch {
      // unreadable or blocked storage: start from the default
    }
    return initial
  })
  useEffect(() => {
    try {
      localStorage.setItem(UI_PREFIX + key, JSON.stringify(value))
    } catch {
      // storage full or blocked: the setting is just not remembered
    }
  }, [key, value])
  return [value, setValue] as const
}

/** `value`, but only after it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return debounced
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true })
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: undefined }))
    fn().then(
      (data) => alive && setState({ data, loading: false }),
      (e) => alive && setState({ error: String(e), loading: false }),
    )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

export function formatLift(lift: number | null | undefined): string {
  if (lift == null) return '–'
  return lift >= 10 ? '>×10' : `×${lift}`
}

export function formatDuration(minutes: number | null | undefined): string {
  if (!minutes) return ''
  const m = Math.floor(minutes)
  const s = Math.round((minutes - m) * 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

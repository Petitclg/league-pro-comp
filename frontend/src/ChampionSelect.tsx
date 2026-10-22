import { useEffect, useMemo, useRef, useState } from 'react'
import { api, selectedLeagues } from './api'
import { Champ } from './Champ'
import { useDDragon } from './ddragon'
import { search } from './fuzzy'
import { useScope } from './scope'

let popularity: { key: string; promise: Promise<Record<string, number>> } | null = null
let knownNames: string[] = []

function loadPopularity(league: string[]): Promise<Record<string, number>> {
  const key = league.join(',')
  if (popularity?.key !== key) {
    const promise = api
      .champions({ league })
      .then((rows) => {
        knownNames = rows.map((r) => r.champion)
        return Object.fromEntries(rows.map((r) => [r.champion, r.picks + r.bans]))
      })
      .catch(() => {
        popularity = null
        return {}
      })
    popularity = { key, promise }
  }
  return popularity.promise
}

export function useChampionNames(): { names: string[]; popularity: Record<string, number> } {
  const dd = useDDragon()
  const { filters } = useScope()
  const leagueKey = selectedLeagues(filters).join(',')
  const [pop, setPop] = useState<Record<string, number>>({})
  useEffect(() => {
    let alive = true
    loadPopularity(leagueKey.split(',')).then((p) => alive && setPop(p))
    return () => {
      alive = false
    }
  }, [leagueKey])
  const names = useMemo(
    () => (dd && dd.names.length ? dd.names : knownNames.length ? knownNames : Object.keys(pop)),
    [dd, pop],
  )
  return { names, popularity: pop }
}

/** Champion autocomplete: typo-tolerant, keyboard navigable, with icons. */
export function ChampionSelect({
  value,
  onChange,
  placeholder = 'Champion…',
  clearOnPick = false,
  exclude = [],
  autoFocus = false,
}: {
  value: string
  onChange: (name: string) => void
  placeholder?: string
  clearOnPick?: boolean
  exclude?: string[]
  autoFocus?: boolean
}) {
  const { names, popularity } = useChampionNames()
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => setText(value), [value])

  const results = useMemo(
    () => search(text === value ? '' : text, names.filter((n) => !exclude.includes(n)), popularity),
    [text, value, names, popularity, exclude],
  )

  const pick = (name: string) => {
    onChange(name)
    setText(clearOnPick ? '' : name)
    setOpen(false)
  }

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) {
        setOpen(false)
        setText(value)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [value])

  return (
    <div className="combo" ref={ref}>
      <input
        value={text}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value)
          setActive(0)
          setOpen(true)
          if (!e.target.value) onChange('')
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setOpen(true)
            setActive((a) => Math.min(a + 1, results.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter' && open && results[active]) {
            e.preventDefault()
            pick(results[active])
          } else if (e.key === 'Escape') {
            setOpen(false)
            setText(value)
          }
        }}
      />
      {open && results.length > 0 && (
        <ul className="combo-list" role="listbox">
          {results.map((n, i) => (
            <li
              key={n}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : ''}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(n)
              }}
            >
              <Champ name={n} size={24} link={false} showName />
            </li>
          ))}
        </ul>
      )}
      {open && results.length === 0 && text && <div className="combo-list muted combo-empty">Aucun champion</div>}
    </div>
  )
}

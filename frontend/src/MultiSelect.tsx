import { useEffect, useMemo, useRef, useState } from 'react'

export interface Option {
  value: string
  label: string
  detail?: string // muted text after the label (not in the summary)
  badge?: string
}

/** Dropdown with checkboxes: pick any number of values, search the list, clear in one click. */
export function MultiSelect({
  label,
  allLabel,
  noun,
  options,
  value,
  onChange,
}: {
  label: string // aria label and menu title
  allLabel: string // shown when nothing is selected
  noun: [string, string] // singular / plural, for "3 équipes"
  options: Option[]
  value: string[]
  onChange: (value: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const labels = useMemo(() => new Map(options.map((o) => [o.value, o.label])), [options])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options
  }, [options, query])

  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v])

  const summary =
    value.length === 0
      ? allLabel
      : value.length <= 2
        ? value.map((v) => labels.get(v) ?? v).join(', ')
        : `${value.length} ${noun[1]}`

  return (
    <div className="multi" ref={ref}>
      <button
        type="button"
        className={`multi-button${value.length ? ' active' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        title={value.map((v) => labels.get(v) ?? v).join('\n') || undefined}
        onClick={() => {
          setOpen(!open)
          setQuery('')
        }}
      >
        <span className="multi-summary">{summary}</span>
        {value.length > 0 && <span className="multi-count">{value.length}</span>}
        <span className="multi-caret">▾</span>
      </button>
      {open && (
        <div className="multi-menu" role="listbox" aria-multiselectable="true" aria-label={label}>
          {options.length > 8 && (
            <input
              autoFocus
              className="multi-search"
              placeholder={`Rechercher (${options.length})…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          <div className="multi-actions">
            <button
              type="button"
              className="link-btn"
              disabled={!shown.length}
              onClick={() => onChange([...new Set([...value, ...shown.map((o) => o.value)])])}
            >
              Tout sélectionner{query ? ' (filtrés)' : ''}
            </button>
            <button type="button" className="link-btn" disabled={!value.length} onClick={() => onChange([])}>
              Tout effacer
            </button>
          </div>
          <ul className="multi-list">
            {shown.map((o) => (
              <li key={o.value}>
                <label>
                  <input type="checkbox" checked={value.includes(o.value)} onChange={() => toggle(o.value)} />
                  <span className="multi-label">{o.label}</span>
                  {o.detail && <small className="muted">{o.detail}</small>}
                  {o.badge && <span className="badge fearless">{o.badge}</span>}
                </label>
              </li>
            ))}
            {shown.length === 0 && <li className="muted multi-empty">Aucun résultat</li>}
          </ul>
        </div>
      )}
    </div>
  )
}

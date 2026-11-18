import type { WpaEvent } from './api'

export const KIND_LABEL: Record<string, string> = {
  kill: 'Kill',
  tower: 'Tour',
  inhibitor: 'Inhibiteur',
  plate: 'Plaque',
  dragon: 'Dragon',
  herald: 'Héraut',
  grub: 'Larves',
  baron: 'Baron',
  atakhan: 'Atakhan',
}

/** Marker letter of an objective on the curve (a kill is a dot). */
export const KIND_LETTER: Record<string, string> = {
  tower: 'T',
  inhibitor: 'I',
  plate: 'P',
  dragon: 'D',
  herald: 'H',
  grub: 'L',
  baron: 'B',
  atakhan: 'A',
}

export const SIDE_LABEL: Record<1 | 2, string> = { 1: 'Blue', 2: 'Red' }

/** 0.61 -> "61 %" */
export function winPct(w: number): string {
  return `${Math.round(w * 100)} %`
}

/** ΔW in signed points with one decimal: 0.048463 -> "+4,8 pts". */
export function points(dw: number): string {
  const value = (Math.abs(dw) * 100).toFixed(1).replace('.', ',')
  return `${dw < 0 ? '−' : '+'}${value} pts`
}

/** 845 -> "14:05" */
export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

/** Blue's W before the event (the API gives W after and ΔW for the acting side). */
export function wBefore(e: WpaEvent): number {
  return e.w - (e.side === 1 ? e.dw : -e.dw)
}

export function eventLabel(e: WpaEvent): string {
  const side = SIDE_LABEL[e.side]
  if (e.kind !== 'kill') return `${KIND_LABEL[e.kind] ?? e.kind} (${side})`
  const victim = e.victim ?? '?'
  return e.champion ? `${e.champion} (${side}) tue ${victim}` : `${victim} exécuté (kill pour ${side})`
}

/** Tooltip of a marker: "14:05 · Tour (Red) · W Blue 61 % → 56 % · +4,8 pts pour Red". */
export function eventTooltip(e: WpaEvent): string {
  return `${clock(e.t)} · ${eventLabel(e)} · W Blue ${winPct(wBefore(e))} → ${winPct(e.w)} · ${points(e.dw)} pour ${SIDE_LABEL[e.side]}`
}

/** The `n` events of largest |ΔW|, largest first (ties: chronological). */
export function keyMoments(events: WpaEvent[], n = 10): WpaEvent[] {
  return events
    .map((e, i) => ({ e, i }))
    .sort((a, b) => Math.abs(b.e.dw) - Math.abs(a.e.dw) || a.i - b.i)
    .slice(0, n)
    .map(({ e }) => e)
}

/** Points of the curve, in seconds: Blue's W after each event and at each minute (which includes the events of its second). */
export function curvePoints(minutes: number[], events: WpaEvent[]): { t: number; w: number }[] {
  return [...minutes.map((w, m) => ({ t: m * 60, w, order: 1 })), ...events.map((e) => ({ t: e.t, w: e.w, order: 0 }))]
    .sort((a, b) => a.t - b.t || a.order - b.order)
    .map(({ t, w }) => ({ t, w }))
}

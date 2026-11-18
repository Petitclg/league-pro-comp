import { describe, expect, it } from 'vitest'
import type { WpaEvent } from './api'
import { clock, curvePoints, eventTooltip, keyMoments, points, winPct } from './winprob'

const tower: WpaEvent = { t: 845, kind: 'tower', side: 2, detail: 'mid', champion: null, victim: null, w: 0.562177, dw: 0.048463 }
const kill: WpaEvent = { t: 912, kind: 'kill', side: 1, detail: null, champion: 'Ahri', victim: 'Zed', w: 0.6, dw: 0.02 }

describe('formatting', () => {
  it('shows W in whole percents and ΔW in signed points', () => {
    expect(winPct(0.610639)).toBe('61 %')
    expect(points(0.048463)).toBe('+4,8 pts')
    expect(points(-0.0012)).toBe('−0,1 pts')
    expect(clock(845)).toBe('14:05')
  })

  it('describes an event as in the specification (example D)', () => {
    expect(eventTooltip(tower)).toBe('14:05 · Tour (Red) · W Blue 61 % → 56 % · +4,8 pts pour Red')
    expect(eventTooltip(kill)).toBe('15:12 · Ahri (Blue) tue Zed · W Blue 58 % → 60 % · +2,0 pts pour Blue')
    expect(eventTooltip({ ...kill, side: 2, champion: null, victim: 'Ahri', dw: 0 })).toContain('Ahri exécuté (kill pour Red)')
  })
})

describe('curve', () => {
  it('lists the 10 biggest swings first', () => {
    const events = Array.from({ length: 12 }, (_, i) => ({ ...kill, t: i, dw: (i % 2 ? -1 : 1) * i * 0.01 }))
    expect(keyMoments(events).map((e) => e.t)).toEqual([11, 10, 9, 8, 7, 6, 5, 4, 3, 2])
  })

  it('merges minute points and events in time order', () => {
    expect(curvePoints([0.5, 0.55], [{ ...kill, t: 30, w: 0.52 }, { ...kill, t: 60, w: 0.6 }])).toEqual([
      { t: 0, w: 0.5 },
      { t: 30, w: 0.52 },
      { t: 60, w: 0.6 },
      { t: 60, w: 0.55 },
    ])
  })
})

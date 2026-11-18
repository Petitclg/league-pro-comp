import { describe, expect, it } from 'vitest'
import { filtersForScope, selectLeagues, SOLOQ, tabVisible } from './scope'

describe('selectLeagues', () => {
  it('combines pro leagues freely', () => {
    expect(selectLeagues(['LCK'], ['LCK', 'LPL'])).toEqual(['LCK', 'LPL'])
    expect(selectLeagues(['LCK', 'LPL'], ['LPL'])).toEqual(['LPL'])
  })

  it('never mixes the solo queue with pro leagues', () => {
    expect(selectLeagues(['LCK', 'LPL'], ['LCK', 'LPL', SOLOQ])).toEqual([SOLOQ])
    expect(selectLeagues([SOLOQ], [SOLOQ, 'LEC'])).toEqual(['LEC'])
    expect(selectLeagues([SOLOQ], [])).toEqual([])
  })
})

describe('filtersForScope', () => {
  it('drops the filters the other scope does not have', () => {
    const f = { league: ['LCK'], team: ['T1'], fearless: 'true', game_number: ['2'], year: ['2025'], rank: ['DIAMOND'] }
    expect(filtersForScope(f, true)).toEqual({ ...f, team: undefined, fearless: undefined, game_number: undefined })
    expect(filtersForScope(f, false)).toEqual({ ...f, rank: undefined })
  })
})

describe('tabVisible', () => {
  it('shows the win probability and items in solo queue only, the countered synergies in pro only', () => {
    expect(tabVisible('winprob', true)).toBe(true)
    expect(tabVisible('winprob', false)).toBe(false)
    expect(tabVisible('items', true)).toBe(true)
    expect(tabVisible('items', false)).toBe(false)
    expect(tabVisible('countered', true)).toBe(false)
    expect(tabVisible('countered', false)).toBe(true)
    expect(tabVisible('drafts', true) && tabVisible('drafts', false)).toBe(true)
  })
})

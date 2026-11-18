import { describe, expect, it } from 'vitest'
import { filtersForScope, selectLeagues, SOLOQ } from './scope'

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

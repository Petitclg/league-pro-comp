import { describe, expect, it } from 'vitest'
import { normalize, search } from './fuzzy'

const NAMES = [
  'Ahri', 'Azir', "Kai'Sa", 'Kalista', 'Twisted Fate', 'Miss Fortune', 'Lee Sin', 'Nidalee',
  'Orianna', 'Nunu & Willump', 'Jarvan IV', "Kha'Zix", 'Wukong', 'Renata Glasc', 'Aurelion Sol',
  'Syndra', 'Sylas', 'Sivir', 'Jax', 'Jhin', 'Jinx',
]

describe('normalize', () => {
  it('ignores case, accents and punctuation', () => {
    expect(normalize("Kai'Sa")).toBe('kaisa')
    expect(normalize('Séraphine')).toBe('seraphine')
  })
})

describe('search', () => {
  it('finds by prefix, ignoring apostrophes', () => {
    expect(search('kai', NAMES)[0]).toBe("Kai'Sa")
    expect(search('kaisa', NAMES)[0]).toBe("Kai'Sa")
    expect(search('khaz', NAMES)[0]).toBe("Kha'Zix")
  })

  it('finds by any word of the name', () => {
    expect(search('fate', NAMES)[0]).toBe('Twisted Fate')
    expect(search('glasc', NAMES)[0]).toBe('Renata Glasc')
  })

  it('tolerates typos and transpositions', () => {
    expect(search('azri', NAMES)[0]).toBe('Azir')
    expect(search('orinana', NAMES)[0]).toBe('Orianna')
    expect(search('syndrs', NAMES)[0]).toBe('Syndra')
    expect(search('wukon', NAMES)[0]).toBe('Wukong')
  })

  it('understands community nicknames', () => {
    expect(search('tf', NAMES)[0]).toBe('Twisted Fate')
    expect(search('mf', NAMES)[0]).toBe('Miss Fortune')
    expect(search('j4', NAMES)[0]).toBe('Jarvan IV')
    expect(search('asol', NAMES)[0]).toBe('Aurelion Sol')
  })

  it('does not match short unrelated input', () => {
    expect(search('qz', NAMES)).toEqual([])
    expect(search('xyz', NAMES)).toEqual([])
  })

  it('ranks by popularity when the query is empty or ambiguous', () => {
    expect(search('', NAMES, { Jinx: 10 })[0]).toBe('Jinx')
    expect(search('j', NAMES, { Jhin: 5, Jinx: 9 }).slice(0, 2)).toEqual(['Jinx', 'Jhin'])
  })
})

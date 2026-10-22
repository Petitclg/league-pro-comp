import { useEffect, useState } from 'react'

export interface DDragon {
  version: string
  ids: Record<string, string> // display name -> image id
  names: string[]
}

let cache: Promise<DDragon> | null = null

function load(): Promise<DDragon> {
  cache ??= (async () => {
    const versions: string[] = await (
      await fetch('https://ddragon.leagueoflegends.com/api/versions.json')
    ).json()
    const version = versions[0]
    const data = await (
      await fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/en_US/champion.json`)
    ).json()
    const ids: Record<string, string> = {}
    for (const c of Object.values<{ id: string; name: string }>(data.data)) ids[c.name] = c.id
    return { version, ids, names: Object.keys(ids).sort() }
  })().catch(() => {
    cache = null
    return { version: '', ids: {}, names: [] }
  })
  return cache
}

export function useDDragon(): DDragon | null {
  const [dd, setDd] = useState<DDragon | null>(null)
  useEffect(() => {
    let alive = true
    load().then((d) => alive && setDd(d))
    return () => {
      alive = false
    }
  }, [])
  return dd
}

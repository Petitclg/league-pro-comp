/** Forgiving champion search: accents, apostrophes, nicknames and typos are tolerated. */

export function normalize(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
}

// Community nicknames -> display name.
const ALIASES: Record<string, string> = {
  mf: 'Miss Fortune',
  tf: 'Twisted Fate',
  j4: 'Jarvan IV',
  jarvan: 'Jarvan IV',
  asol: 'Aurelion Sol',
  ww: 'Warwick',
  nunu: 'Nunu & Willump',
  cass: 'Cassiopeia',
  cait: 'Caitlyn',
  lb: 'LeBlanc',
  kog: "Kog'Maw",
  kha: "Kha'Zix",
  vlad: 'Vladimir',
  trynd: 'Tryndamere',
  morde: 'Mordekaiser',
  fiddle: 'Fiddlesticks',
  gp: 'Gangplank',
  heimer: 'Heimerdinger',
  ez: 'Ezreal',
  mundo: 'Dr. Mundo',
  tk: 'Tahm Kench',
  tahm: 'Tahm Kench',
  xin: 'Xin Zhao',
  monkeyking: 'Wukong',
  renata: 'Renata Glasc',
  glasc: 'Renata Glasc',
  voli: 'Volibear',
  malz: 'Malzahar',
  malph: 'Malphite',
  morg: 'Morgana',
  sej: 'Sejuani',
  liss: 'Lissandra',
  yi: 'Master Yi',
  masteryi: 'Master Yi',
  sera: 'Seraphine',
  ori: 'Orianna',
  naut: 'Nautilus',
  lee: 'Lee Sin',
  bel: "Bel'Veth",
  rek: "Rek'Sai",
  chogath: "Cho'Gath",
  velkoz: "Vel'Koz",
  ksante: "K'Sante",
  kaisa: "Kai'Sa",
  reksai: "Rek'Sai",
  khazix: "Kha'Zix",
  kogmaw: "Kog'Maw",
  belveth: "Bel'Veth",
}

const ALIASES_BY_NAME: Record<string, string[]> = {}
for (const [alias, name] of Object.entries(ALIASES)) (ALIASES_BY_NAME[name] ??= []).push(alias)

function distance(a: string, b: string): number {
  // Optimal string alignment distance (Levenshtein + adjacent transpositions).
  const m = a.length
  const n = b.length
  const d: number[][] = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)])
  for (let j = 1; j <= n; j++) d[0][j] = j
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 0.5)
      }
    }
  }
  return d[m][n]
}

function isSubsequence(q: string, s: string): boolean {
  let i = 0
  for (const c of s) if (c === q[i]) i++
  return i === q.length
}

/** Score of `query` against a champion name (0 = no match). Higher is better. */
export function score(query: string, name: string, extraKeys: string[] = []): number {
  const q = normalize(query)
  if (!q) return 0
  const keys = [normalize(name), ...extraKeys.map(normalize)]
  const words = name.split(/[\s'.&-]+/).map(normalize).filter(Boolean)
  let best = 0
  for (const k of keys) {
    if (k === q) best = Math.max(best, 100)
    else if (k.startsWith(q)) best = Math.max(best, 90)
    else if (k.includes(q)) best = Math.max(best, 70)
  }
  if (words.some((w) => w.startsWith(q))) best = Math.max(best, 80)
  if (best) return best
  if (q.length >= 3 && keys.some((k) => isSubsequence(q, k))) best = 45
  // Typos: compare with the whole name and with its same-length prefix.
  const tolerance = q.length <= 3 ? 0 : q.length <= 5 ? 1 : 2
  if (tolerance) {
    for (const k of [...keys, ...words]) {
      const d = Math.min(distance(q, k), distance(q, k.slice(0, q.length)) + 0.5)
      if (d <= tolerance) best = Math.max(best, 60 - d * 10)
    }
  }
  return best
}

export function aliasesFor(name: string): string[] {
  return ALIASES_BY_NAME[name] ?? []
}

export function search(
  query: string,
  names: string[],
  popularity: Record<string, number> = {},
  limit = 8,
): string[] {
  if (!normalize(query)) {
    return [...names].sort((a, b) => (popularity[b] ?? 0) - (popularity[a] ?? 0) || a.localeCompare(b)).slice(0, limit)
  }
  return names
    .map((n) => ({ n, s: score(query, n, aliasesFor(n)) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || (popularity[b.n] ?? 0) - (popularity[a.n] ?? 0) || a.n.localeCompare(b.n))
    .slice(0, limit)
    .map((x) => x.n)
}

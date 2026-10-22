export type Role = 'top' | 'jungle' | 'mid' | 'bot' | 'support'

export const ROLES: Role[] = ['top', 'jungle', 'mid', 'bot', 'support']
export const ROLE_LABEL: Record<Role, string> = {
  top: 'Top',
  jungle: 'Jungle',
  mid: 'Mid',
  bot: 'Bot',
  support: 'Support',
}

export interface LeagueInfo {
  key: string
  name: string
  region: string
  level: string // 'major' | 'secondary' | 'primary' | 'other' | 'showmatch'
  games: number
  first: string
  last: string
}

export interface Meta {
  selected: string[] // leagues analysed together
  leagues: LeagueInfo[] // every league in the database
  roles: Role[]
  games: number
  series: number
  teams: string[]
  years: string[]
  tournaments: { name: string; fearless: boolean }[]
  patches: string[]
  fearless_since: string | null
  version: string
  desktop: boolean // running inside the desktop app (the window pings the server)
  registry: { key: string; name: string; region: string }[] // every league the app can import
}

export interface ImportStatus {
  state: 'idle' | 'running' | 'done' | 'error' | 'cancelled'
  leagues?: string[]
  years?: [number, number]
  done?: number
  total?: number
  games?: number
  current?: string | null
  log?: string[]
  error?: string | null
}

export const DEFAULT_LEAGUE = 'LCK'

/** Global scope applied to every statistic. List filters keep a game matching ANY selected value. */
export interface Filters {
  league?: string[]
  team?: string[]
  tournament?: string[]
  year?: string[]
  patch?: string[]
  fearless?: string // 'true' | 'false'
  game_number?: string[]
}

export type ListFilter = 'league' | 'team' | 'tournament' | 'year' | 'patch' | 'game_number'

/** Filters read from older sessions held single strings: turn them into lists. */
export function normalizeFilters(raw: unknown): Filters {
  const out: Filters = {}
  if (!raw || typeof raw !== 'object') return out
  const r = raw as Record<string, unknown>
  if (typeof r.fearless === 'string') out.fearless = r.fearless
  for (const k of ['league', 'team', 'tournament', 'year', 'patch', 'game_number'] as ListFilter[]) {
    const v = r[k]
    const list = Array.isArray(v) ? v.map(String) : typeof v === 'string' && v ? [v] : []
    if (list.length) out[k] = list
  }
  return out
}

/** The leagues in use: the selection, or the default league when none is chosen. */
export const selectedLeagues = (f: Filters): string[] => (f.league?.length ? f.league : [DEFAULT_LEAGUE])

export interface PickInfo {
  order: number
  slot: number // 1..10 in draft order (blue has the first pick)
  role: Role
  champion: string
  player: string | null
  kills: number | null
  deaths: number | null
  assists: number | null
}

export interface SideDraft {
  team: string
  win: boolean | null
  stats: { kills?: number; gold?: number; towers?: number; dragons?: number; barons?: number }
  bans: { champion: string; order: number }[]
  picks: PickInfo[]
}

export type HighlightType = 'synergy' | 'counter' | 'signature' | 'rare'

export interface Highlight {
  type: HighlightType
  side: 1 | 2
  champions: string[]
  text: string
  detail: string
}

export interface GameData {
  game_id: string
  game_number: number | null
  date: string | null
  patch: string | null
  duration: number | null
  fearless: boolean
  locked: string[]
  winner: number | null
  matched: boolean
  sides: Record<'1' | '2', SideDraft>
  highlights: Highlight[]
}

export interface SeriesData {
  match_id: string
  league: string
  tournament: string
  stage: string | null
  date: string | null
  best_of: number | null
  fearless: boolean
  teams: [string, string]
  score: [number, number]
  games: GameData[]
}

export interface SeriesQuery {
  champion?: string
  role?: string
  with?: string
  versus?: string
  game?: string
  limit?: number
  offset?: number
}

export interface Strength {
  score: number
  tier: 'S' | 'A' | 'B' | 'C' | 'D' | null
  wr_adjusted: number
  wr_low: number | null
  wr_high: number | null
  significant_edge: boolean
}

export interface Spot {
  dimension: string
  label: string
  games: number
  wins: number
  winrate: number
  baseline: number
  delta: number
  z: number
  link: { kind: 'synergy' | 'counter'; other: string } | null
}

export interface ChampionRow {
  champion: string
  picks: number
  bans: number
  winrate: number | null
  pick_rate: number | null
  ban_rate: number | null
  presence: number | null
  roles: Partial<Record<Role, number>>
  strength: Strength
}

export interface WR {
  games: number
  winrate: number | null
}

export interface ChampionDetail {
  champion: string
  games_in_scope: number
  picks: number
  bans: number
  winrate: number | null
  pick_rate: number | null
  ban_rate: number | null
  presence: number | null
  strength: Strength
  spots: { baseline: number | null; hot: Spot[]; cold: Spot[] }
  avg_duration: number | null
  avg_pick_slot: number | null
  first_rotation_rate: number | null
  ban_phase1_rate: number | null
  avg_ban_order: number | null
  stats: {
    kda: number | null
    kills: number | null
    deaths: number | null
    assists: number | null
    cs_min: number | null
    gpm: number | null
    dpm: number | null
    damage_share: number | null
    kill_participation: number | null
  }
  roles: ({ role: Role } & WR)[]
  sides: { blue?: WR; red?: WR }
  by_game_number: ({ game_number: number } & WR)[]
  by_patch: {
    patch: string
    games: number
    bans: number
    pick_rate: number | null
    ban_rate: number | null
    winrate: number | null
  }[]
  players: ({ player: string; kda: number | null } & WR)[]
  teams: ({ team: string } & WR)[]
  banned_by: { team: string; bans: number }[]
  banned_against: { team: string; bans: number }[]
  partners: {
    role: Role
    own_role: Role
    champion: string
    games: number
    winrate: number | null
    lift: number
  }[]
  matchups: { role: Role; champion: string; games: number; wins: number; winrate: number | null }[]
}

export interface PairRow {
  a: { champion: string; role: Role }
  b: { champion: string; role: Role }
  games: number
  pick_rate: number
  winrate: number | null
  lift: number
  /** (played + excess bans) / games of the scope: the duo's "presence" */
  presence: number | null
  presence_played: number | null
  presence_countered: number | null
  /** games with A or B banned, and how many independent bans would give (same patch) */
  bans: { either: number; expected: number }
  /** games added to the presence: bans beyond chance */
  countered: number
  /** played / (played + excess bans) */
  realised_share: number | null
}

export interface DuoDetail {
  kind: 'synergy' | 'counter'
  a: string
  b: string
  games: number
  wins: number
  winrate: number | null
  roles: { a_role: Role; b_role: Role; games: number }[]
}

export interface DenialMeasure {
  count: number
  rate: number | null
  expected_rate: number | null
  lift: number | null
  z: number
}

export interface DenialSummary {
  available: number
  outcomes: Record<
    'banned_before' | 'combo' | 'deny_pick' | 'ban_after' | 'untouched' | 'prior_combo' | 'taken_before',
    number
  >
  measures: Record<'anticipation' | 'reaction' | 'removed' | 'combo', DenialMeasure>
}

export interface DenialDirection extends DenialSummary {
  trigger: string
  partner: string
  picked: number
  splits: ({ label: string } & DenialSummary)[]
}

export interface NeutralGroup {
  label: string
  n: number
  a_banned: number
  b_banned: number
  both_banned: number
  either_banned: number
  either_rate: number | null
  expected_either_rate: number | null
  expected_both_banned: number
  z: number
  free: number
  together: number
  split: number
  one_picked: number
  none_picked: number
}

export interface Neutralisation {
  a: string
  b: string
  groups: NeutralGroup[]
}

export interface DenialResult {
  a_to_b: DenialDirection
  b_to_a: DenialDirection
  neutralisation: Neutralisation
}

export type RankingLevel = 'strong' | 'likely' | 'hint'

export interface CounteredItem extends DenialMeasure {
  level: RankingLevel
  trigger: string
  partner: string
  co_picks: number
  available: number
  splits: { label: string; available: number; rate: number | null; expected_rate: number | null; lift: number | null; z: number }[]
}

export interface CounteredList {
  by_level: Record<RankingLevel, number>
  items: CounteredItem[]
}

export interface CounteredRanking {
  tested: number
  lift_min: number
  levels: { key: RankingLevel; z: number; min_count: number; by_chance: number }[]
  neutralised: {
    tested: number
    by_level: Record<RankingLevel, number>
    chance: Record<RankingLevel, number>
    items: { level: RankingLevel; a: string; b: string; co_picks: number; groups: NeutralGroup[] }[]
  }
  anticipation: CounteredList
  reaction: CounteredList
}

export interface RecordItem {
  champions: string[]
  label: string
  value: string
  sub: string
  link?: 'synergy' | 'counter'
  game_id?: string
}

export interface RecordSection {
  id: string
  title: string
  description: string
  items: RecordItem[]
}

export interface Synergy {
  role: Role
  champion: string
  games: number
  winrate: number | null
}

export interface Suggestion {
  champion: string
  score: number
  share: number
  games: number
  winrate: number | null
  counter_winrate: number | null
  counter_games: number
  synergies: Synergy[]
}

export interface SuggestResult {
  matching_comps: number
  suggestions: Partial<Record<Role, Suggestion[]>>
  similar_comps: {
    match: number
    game_id: string
    team: string
    opponent: string
    win: boolean | null
    date: string | null
    game_number: number | null
    picks: Record<Role, string>
  }[]
  closest_teams: { team: string; similarity: number; best_match: number; games: number }[]
}

export interface LiveState {
  connected: boolean
  in_champ_select: boolean
  reason?: string
  phase?: string
  my_team?: Partial<Record<Role, string>>
  my_team_unplaced?: string[]
  enemy_picks?: string[]
  bans?: string[]
}

function qs(params: object): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (Array.isArray(v)) v.forEach((item) => sp.append(k, String(item))) // repeated parameter
    else if (v !== undefined && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  importStatus: () => request<ImportStatus>('/api/import'),
  startImport: (body: { leagues: string[]; first_year: number }) =>
    request<ImportStatus>('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  cancelImport: () => request<ImportStatus>('/api/import/cancel', { method: 'POST' }),
  heartbeat: () => fetch('/api/heartbeat', { method: 'POST' }).catch(() => undefined),
  meta: (leagues: string[]) => request<Meta>(`/api/meta${qs({ league: leagues })}`),
  series: (f: Filters, q: SeriesQuery) =>
    request<{ total: number; series: SeriesData[] }>(`/api/series${qs({ ...f, ...q })}`),
  champions: (f: Filters) => request<ChampionRow[]>(`/api/champions${qs(f)}`),
  champion: (name: string, f: Filters) =>
    request<ChampionDetail>(`/api/champions/${encodeURIComponent(name)}${qs(f)}`),
  pairs: (f: Filters, p: Record<string, string | number | undefined>) =>
    request<{ comps: number; pairs: PairRow[] }>(`/api/pairs${qs({ ...f, ...p })}`),
  duo: (f: Filters, kind: string, a: string, b: string) =>
    request<DuoDetail>(`/api/duo${qs({ ...f, kind, a, b })}`),
  records: (f: Filters) => request<RecordSection[]>(`/api/records${qs(f)}`),
  denial: (f: Filters, a: string, b: string) => request<DenialResult>(`/api/denial${qs({ ...f, a, b })}`),
  countered: (f: Filters) => request<CounteredRanking>(`/api/countered-synergies${qs({ ...f, limit: 20 })}`),
  live: () => request<LiveState>('/api/live'),
  suggest: (
    body: {
      picks: Partial<Record<Role, string>>
      enemy: string[]
      unavailable: string[]
    } & Filters,
  ) =>
    request<SuggestResult>(`/api/suggest${qs({ league: body.league })}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        picks: body.picks,
        enemy: body.enemy,
        unavailable: body.unavailable,
        teams: body.team ?? [],
        tournaments: body.tournament ?? [],
        years: body.year ?? [],
        patches: body.patch ?? [],
        fearless: body.fearless === undefined ? undefined : body.fearless === 'true',
      }),
    }),
}

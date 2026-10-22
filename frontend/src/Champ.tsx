import type { ReactNode } from 'react'
import { useDDragon } from './ddragon'
import { href } from './router'

export function Champ({
  name,
  size = 40,
  banned = false,
  showName = false,
  link = true,
  ring,
  badge,
  title,
}: {
  name: string
  size?: number
  banned?: boolean
  showName?: boolean
  link?: boolean
  ring?: string
  badge?: ReactNode
  title?: string
}) {
  const dd = useDDragon()
  const id = dd?.ids[name]
  const img = id ? (
    <img
      src={`https://ddragon.leagueoflegends.com/cdn/${dd!.version}/img/champion/${id}.png`}
      width={size}
      height={size}
      alt={name}
      loading="lazy"
    />
  ) : (
    <span className="champ-fallback" style={{ width: size, height: size }}>
      {name.slice(0, 2)}
    </span>
  )
  const body = (
    <>
      <span className={`champ-img${ring ? ` ring ring-${ring}` : ''}`}>
        {img}
        {badge}
      </span>
      {showName && <span className="champ-name">{name}</span>}
    </>
  )
  const className = `champ${banned ? ' banned' : ''}`
  return link ? (
    <a className={className} href={href('champion', name)} title={title ?? name}>
      {body}
    </a>
  ) : (
    <span className={className} title={title ?? name}>
      {body}
    </span>
  )
}

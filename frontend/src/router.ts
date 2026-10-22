import { useEffect, useState } from 'react'

export interface Route {
  parts: string[]
}

function read(): Route {
  const path = window.location.hash.replace(/^#\/?/, '')
  return { parts: path.split('/').filter(Boolean).map(decodeURIComponent) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function href(...parts: string[]): string {
  return '#/' + parts.map(encodeURIComponent).join('/')
}

export function navigate(...parts: string[]) {
  window.location.hash = href(...parts)
}

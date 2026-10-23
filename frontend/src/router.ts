import { useEffect, useState } from 'react'

export interface Route {
  parts: string[]
}

const LAST_ROUTE = 'lolpro-ui:last-route'

// A launch without a page in the address (the desktop app opens the root) goes back to where the user left.
if (typeof window !== 'undefined' && !window.location.hash) {
  try {
    const last = localStorage.getItem(LAST_ROUTE)
    if (last) window.location.replace(last)
  } catch {
    // storage blocked: open the default page
  }
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
  useEffect(() => {
    try {
      if (window.location.hash) localStorage.setItem(LAST_ROUTE, window.location.hash)
    } catch {
      // storage blocked: the page is just not remembered
    }
  }, [route])
  return route
}

export function href(...parts: string[]): string {
  return '#/' + parts.map(encodeURIComponent).join('/')
}

export function navigate(...parts: string[]) {
  window.location.hash = href(...parts)
}

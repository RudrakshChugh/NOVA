// A sliding switch: left is light, right is dark.
//
// Light is the default and the operating system is not consulted. A dark interface is a choice
// about a particular screen in a particular room, and this one is used to proof a printed paper
// — so the first visit shows the paper-coloured version, and dark is there for whoever wants it.

import { useEffect, useState } from 'react'
import Icon from './Icon'

const KEY = 'theme'

function initial() {
  try { return localStorage.getItem(KEY) === 'dark' } catch { return false }  // private window
}

export default function ThemeSwitch() {
  const [dark, setDark] = useState(initial)

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    try { localStorage.setItem(KEY, dark ? 'dark' : 'light') } catch { /* private window */ }
  }, [dark])

  return (
    <button
      type="button" className="theme-switch" role="switch" aria-checked={dark}
      aria-label="Dark mode" title={dark ? 'Switch to light' : 'Switch to dark'}
      onClick={() => setDark(!dark)}
    >
      <span className="track">
        <Icon name="sun" size={13} className="t-sun" />
        <Icon name="moon" size={13} className="t-moon" />
        <span className="knob" />
      </span>
    </button>
  )
}

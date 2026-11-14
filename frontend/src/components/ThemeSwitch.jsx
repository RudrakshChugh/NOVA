// A sliding switch: left is light, right is dark.
//
// Until someone touches it there is no stored choice and the page follows the operating system,
// so the knob has to start on whichever side the system has actually put us — and keep up if the
// system flips while the tab is open. Touching it writes a choice that then overrides the system.

import { useEffect, useState } from 'react'
import Icon from './Icon'

const KEY = 'theme'
const systemDark = () => window.matchMedia('(prefers-color-scheme: dark)').matches

function initial() {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'dark' || t === 'light') return { dark: t === 'dark', chosen: true }
  } catch { /* private window */ }
  return { dark: systemDark(), chosen: false }
}

export default function ThemeSwitch() {
  const [{ dark, chosen }, setState] = useState(initial)

  // While no choice has been made, track the system rather than freezing at whatever it was
  // when the tab opened.
  useEffect(() => {
    if (chosen) return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const sync = () => setState({ dark: mq.matches, chosen: false })
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [chosen])

  useEffect(() => {
    if (!chosen) return                       // no choice: leave the stylesheet to the system
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    try { localStorage.setItem(KEY, dark ? 'dark' : 'light') } catch { /* private window */ }
  }, [dark, chosen])

  return (
    <button
      type="button" className="theme-switch" role="switch" aria-checked={dark}
      aria-label="Dark mode" title={dark ? 'Switch to light' : 'Switch to dark'}
      onClick={() => setState({ dark: !dark, chosen: true })}
    >
      <span className="track">
        <Icon name="sun" size={13} className="t-sun" />
        <Icon name="moon" size={13} className="t-moon" />
        <span className="knob" />
      </span>
    </button>
  )
}

// The small set of primitives every page is built from. Each one is a thin wrapper over the
// classes in index.css, so markup stays readable and a styling change lands in one place.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import Icon from './Icon'

/* ─── layout ─────────────────────────────────────────────────────────────────────────────── */

export function Card({ title, step, hint, actions, children, foot, pad = true, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div className="ht">
            {step
              ? <div className="subhead"><span className="step-n">{step}</span><h3>{title}</h3></div>
              : <h3>{title}</h3>}
            {hint && <p className="hint">{hint}</p>}
          </div>
          {actions && <div className="actions">{actions}</div>}
        </header>
      )}
      {children != null && <div className={`card-body${pad ? '' : ' flush'}`}>{children}</div>}
      {foot && <footer className="card-foot">{foot}</footer>}
    </section>
  )
}

export function PageHead({ eyebrow, title, lede, actions }) {
  return (
    <header className="page-head">
      <div className="row top">
        <div style={{ flex: 1, minWidth: 0 }}>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h1>{title}</h1>
          {lede && <p className="lede">{lede}</p>}
        </div>
        {actions && <div className="row tight">{actions}</div>}
      </div>
    </header>
  )
}

/** The "what happens here" copy, folded away by default once it has been read once. */
export function Explainer({ id, title = 'How this step works', children }) {
  const key = `explainer:${id}`
  const [open, setOpen] = useState(() => localStorage.getItem(key) !== 'closed')
  const toggle = (e) => {
    setOpen(e.target.open)
    localStorage.setItem(key, e.target.open ? 'open' : 'closed')
  }
  return (
    <details className="explainer" open={open} onToggle={toggle}>
      <summary><Icon name="chevronRight" size={15} className="chev" />{title}</summary>
      <div className="body">{children}</div>
    </details>
  )
}

/* ─── controls ───────────────────────────────────────────────────────────────────────────── */

export function Btn({ variant = '', size = '', icon, busy, children, className = '', ...rest }) {
  const cls = ['btn', variant && `btn-${variant}`, size && `btn-${size}`, !children && 'btn-icon', className]
  return (
    <button className={cls.filter(Boolean).join(' ')} disabled={rest.disabled || busy} {...rest}>
      {busy ? <span className="spinner" /> : icon && <Icon name={icon} size={size === 'sm' ? 14 : 16} />}
      {children}
    </button>
  )
}

export function Field({ label, hint, inline, grow, children }) {
  return (
    <label className={`field${inline ? ' inline' : ''}${grow ? ' grow' : ''}`}>
      {label && <span className="lbl">{label}</span>}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count != null && <span className="count">{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value} role="tab" aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
        >
          {t.icon && <Icon name={t.icon} size={15} />}
          {t.label}
          {t.badge != null && <span className={`tab-badge${t.badgeTone ? ` ${t.badgeTone}` : ''}`}>{t.badge}</span>}
        </button>
      ))}
    </div>
  )
}

/* ─── display ────────────────────────────────────────────────────────────────────────────── */

export function Badge({ tone = 'neutral', dot, children }) {
  return <span className={`badge badge-${tone}`}>{dot && <span className="dot" />}{children}</span>
}

export function Note({ tone = 'info', icon, children }) {
  const fallback = { info: 'info', warn: 'alert', bad: 'alert', ok: 'check' }[tone]
  return (
    <div className={`note note-${tone}`}>
      <Icon name={icon || fallback} size={17} />
      <div>{children}</div>
    </div>
  )
}

export function Stat({ label, value, hint, tone = '' }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

export function Meter({ value, tone = '', show = true }) {
  const v = Math.max(0, Math.min(1, value ?? 0))
  return (
    <span className="meter-row">
      <span className={`meter ${tone}`}><i style={{ width: `${v * 100}%` }} /></span>
      {show && <span className="num">{value?.toFixed(2)}</span>}
    </span>
  )
}

export function Progress({ value, max, tone = '' }) {
  const pct = max > 0 ? Math.min(1, value / max) : 0
  return <div className={`progress ${tone}`} role="progressbar" aria-valuenow={value} aria-valuemax={max}><i style={{ width: `${pct * 100}%` }} /></div>
}

export function Table({ head, children, className = '' }) {
  return (
    <div className={`table-wrap ${className}`}>
      <table>
        {head && <thead><tr>{head.map((h, i) => (
          typeof h === 'string'
            ? <th key={i}>{h}</th>
            : <th key={i} className={h.num ? 'num' : undefined} style={h.width ? { width: h.width } : undefined}>{h.label}</th>
        ))}</tr></thead>}
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Empty({ icon = 'file', title, children, action }) {
  return (
    <div className="empty">
      <Icon name={icon} size={30} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div className="row">{action}</div>}
    </div>
  )
}

export function Kbd({ children }) {
  return <kbd>{children}</kbd>
}

/* ─── toasts ─────────────────────────────────────────────────────────────────────────────── */

const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastHost({ children }) {
  const [items, setItems] = useState([])
  const seq = useRef(0)

  const push = useCallback((tone, title, detail) => {
    const id = ++seq.current
    setItems((xs) => [...xs, { id, tone, title, detail }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === 'bad' ? 9000 : 5000)
  }, [])

  const close = (id) => setItems((xs) => xs.filter((x) => x.id !== id))

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="false">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <Icon name={t.tone === 'bad' ? 'alert' : t.tone === 'ok' ? 'check' : 'info'} size={17} />
            <div className="body">
              <strong>{t.title}</strong>
              {t.detail && <span>{t.detail}</span>}
            </div>
            <button onClick={() => close(t.id)} aria-label="Dismiss"><Icon name="x" size={15} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

/* ─── keyboard ───────────────────────────────────────────────────────────────────────────── */

/** Window-level shortcuts, suppressed while the caret is in a field or a modifier is held, which
 *  is the convention annotation tools settle on so typing a rejection reason never triggers one. */
export function useShortcuts(map, enabled = true) {
  const ref = useRef(map)
  useEffect(() => { ref.current = map })
  useEffect(() => {
    if (!enabled) return
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target
      if (t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
      const fn = ref.current[e.key.toLowerCase()] || ref.current[e.key]
      if (!fn) return
      e.preventDefault()
      fn()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}

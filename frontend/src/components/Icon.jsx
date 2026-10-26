// One inline SVG sprite, drawn on a 24-unit grid with a 1.75 stroke so every icon in the interface
// has the same weight. Inlined rather than pulled from a package: a dozen paths is not a dependency.

const PATHS = {
  upload: 'M12 15V3m-5 5 5-5 5 5M3 15v3a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3v-3',
  blueprint: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 9h18M9 9v12',
  generate: 'M12 3v4M12 17v4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M3 12h4M17 12h4M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8',
  review: 'M9 11.5l2.5 2.5L16 9M3 6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3z',
  bank: 'M3 8.5 12 4l9 4.5M5 10v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8M9 19v-5h6v5',
  engines: 'M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M6 6a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1zM10 10h4v4h-4z',
  evaluation: 'M9 3h6M10 3v5.5L5.5 17A2.5 2.5 0 0 0 7.8 21h8.4a2.5 2.5 0 0 0 2.3-4L14 8.5V3M7 15h10',
  logout: 'M15 17l5-5-5-5M20 12H9M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6',
  chevronRight: 'm9 5 7 7-7 7',
  chevronDown: 'm5 9 7 7 7-7',
  check: 'm5 13 4.5 4.5L19 7',
  x: 'M6 6l12 12M18 6 6 18',
  alert: 'M12 9v4m0 3h.01M10.3 3.9 2.6 17.1A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0z',
  info: 'M12 16v-5h-.5M11.9 8h.2M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z',
  file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4',
  papers: 'M8 3h8l4 4v10a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM16 3v4h4M4 7v12a3 3 0 0 0 3 3h9',
  plus: 'M12 5v14M5 12h14',
  trash: 'M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6',
  play: 'M8 5.3v13.4L19 12z',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z',
  keyboard: 'M4 7h16a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zM7 11h.01M11 11h.01M15 11h.01M17 11h.01M8 14h8',
  target: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0 3.5a.5.5 0 1 0 0 1 .5.5 0 0 0 0-1z',
  shield: 'M12 3 5 6v5.5c0 4.2 2.9 7.6 7 9.5 4.1-1.9 7-5.3 7-9.5V6z M9 12l2 2 4-4',
  menu: 'M4 7h16M4 12h16M4 17h16',
  arrowRight: 'M5 12h14m-6-7 7 7-7 7',
  download: 'M12 3v12m-4.5-4.5L12 15l4.5-4.5M4 19h16',
  clock: 'M12 7v5l3 2M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z',
  sort: 'M7 4v16m0 0-3-3m3 3 3-3M17 20V4m0 0-3 3m3-3 3 3',
}

export default function Icon({ name, size = 18, className = '', title, style }) {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg
      className={`ico ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined} focusable="false"
      style={style}
    >
      {title && <title>{title}</title>}
      <path d={d} />
    </svg>
  )
}

// Shared rendering for the interval-carrying numbers the evaluation protocol reports.
// An undefined statistic arrives as null and is shown as a dash, never as zero.

export function Interval({ i, percent = false }) {
  if (!i || i.point == null) return <span className="muted">—</span>
  const fmt = (x) => (percent ? `${(x * 100).toFixed(1)}%` : x.toFixed(3))
  if (i.low == null || i.high == null) return <>{fmt(i.point)}</>
  return (
    <>
      {fmt(i.point)} <span className="muted">[{fmt(i.low)}, {fmt(i.high)}]</span>
    </>
  )
}

export function Verdict({ text }) {
  const excludesZero = text?.includes('excludes zero')
  return <span className={`pill ${excludesZero ? 'ok' : 'neutral'}`}>{text}</span>
}

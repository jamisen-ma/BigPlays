import { hypeColor } from './util'

export function HypeMeter({ value, size = 64, stroke = 6, label = 'HYPE' }: { value: number; size?: number; stroke?: number; label?: string }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(1, value))
  return (
    <div className="hype" style={{ width: size, height: size }} title={`hype score ${v.toFixed(2)}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--line-2)" strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2} cy={size / 2} r={r}
          stroke={hypeColor(v)} strokeWidth={stroke} fill="none" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - v)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className="hype-arc"
        />
      </svg>
      <div className="hype-text">
        <span className="hype-num" style={{ color: hypeColor(v) }}>{Math.round(v * 100)}</span>
        <span className="hype-label">{label}</span>
      </div>
    </div>
  )
}

export function HypeBar({ value, label }: { value: number; label: string }) {
  const v = Math.max(0, Math.min(1, value))
  return (
    <div className="hbar">
      <div className="hbar-head"><span>{label}</span><span className="mono">{v.toFixed(2)}</span></div>
      <div className="hbar-track"><div className="hbar-fill" style={{ width: `${v * 100}%`, background: hypeColor(v) }} /></div>
    </div>
  )
}

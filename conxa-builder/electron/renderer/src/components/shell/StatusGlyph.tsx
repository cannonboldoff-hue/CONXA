import { STATUS, type StatusKind } from '@/lib/status'

/** Shape plus colour, never colour alone. `label` is read by screen readers. */
export function StatusGlyph({ kind, label, className }: { kind: StatusKind; label?: string; className?: string }) {
  const s = STATUS[kind]
  return (
    <span
      role="img"
      aria-label={label ?? s.label}
      title={label ?? s.label}
      className={className}
      style={{ color: s.color, fontSize: 12, lineHeight: 1, width: 12, textAlign: 'center', flexShrink: 0 }}
    >
      {s.glyph}
    </span>
  )
}

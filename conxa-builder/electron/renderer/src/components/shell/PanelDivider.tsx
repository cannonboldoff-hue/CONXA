import { useRef } from 'react'
import { cn } from '@/lib/utils'

/**
 * A focusable vertical divider. Drag to resize, arrow keys move it 16 px, double-click resets.
 * `grow` is the direction a rightward drag changes the neighbouring panel: +1 when the panel is
 * on the divider's left (navigation), -1 when it is on the right (inspector).
 */
export function PanelDivider({
  label,
  value,
  min,
  max,
  grow,
  onChange,
  onNudge,
  onReset,
}: {
  label: string
  value: number
  min: number
  max: number
  grow: 1 | -1
  onChange: (px: number) => void
  onNudge: (dir: 1 | -1) => void
  onReset: () => void
}) {
  const drag = useRef<{ x: number; start: number } | null>(null)

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className={cn(
        'group relative z-20 w-px shrink-0 cursor-col-resize bg-[var(--border-subtle)] outline-none',
        'before:absolute before:inset-y-0 before:-left-1.5 before:-right-1.5 before:content-[""]',
        'hover:bg-white/[0.18] focus-visible:bg-brand focus-visible:shadow-none',
      )}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId)
        drag.current = { x: e.clientX, start: value }
      }}
      onPointerMove={(e) => {
        if (!drag.current) return
        onChange(drag.current.start + grow * (e.clientX - drag.current.x))
      }}
      onPointerUp={() => {
        drag.current = null
      }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') {
          e.preventDefault()
          onNudge(grow)
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault()
          onNudge(grow === 1 ? -1 : 1)
        }
      }}
    />
  )
}

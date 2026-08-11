import type { ReactNode } from 'react'

export function Panel({
  children, className = '', title, right, plain = false,
}: {
  children: ReactNode
  className?: string
  title?: ReactNode
  right?: ReactNode
  plain?: boolean
}) {
  return (
    <div className={`panel grain ${plain ? 'panel-plain' : ''} ${className}`}>
      {title !== undefined && (
        <div className="flex items-center justify-between gap-2 px-3 pt-2.5 pb-1.5
                        border-b border-[var(--color-line)]">
          <div className="survey text-[10px] text-[var(--color-ink-dim)]">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  )
}

export function StampButton({
  children, onClick, primary = false, on = false, disabled = false, title,
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  primary?: boolean
  on?: boolean
  disabled?: boolean
  title?: string
  className?: string
}) {
  return (
    <button
      className={`stamp px-2.5 py-1 text-[12px] ${className}`}
      data-primary={primary} data-on={on} disabled={disabled}
      onClick={onClick} title={title}
    >
      {children}
    </button>
  )
}

/** A lane mark. Diamonds, because circles all look alike at 7px. */
export function Diamond({ color, size = 7 }: { color: string; size?: number }) {
  return <span className="diamond" style={{ background: color, width: size, height: size }} />
}

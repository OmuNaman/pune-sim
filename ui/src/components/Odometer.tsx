import { useEffect, useRef, useState } from 'react'

/**
 * The clock. Digits roll rather than blink, because a clock that jumps reads
 * as a re-render and a clock that rolls reads as time passing.
 *
 * Each character gets its own column so only the digits that changed move —
 * rolling the whole string would make 08:59 -> 09:00 look like a slot machine.
 */
function Digit({ ch }: { ch: string }) {
  const [prev, setPrev] = useState(ch)
  const [rolling, setRolling] = useState(false)
  const timer = useRef<number>(0)

  useEffect(() => {
    if (ch === prev) return
    setRolling(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { setPrev(ch); setRolling(false) }, 110)
    return () => window.clearTimeout(timer.current)
  }, [ch, prev])

  if (ch === ':') return <span className="opacity-45 px-[1px]">:</span>

  return (
    <span className="relative inline-block overflow-hidden align-baseline"
          style={{ width: '0.62em', height: '1.05em' }}>
      <span
        className="absolute inset-x-0 transition-transform duration-100 ease-out"
        style={{ transform: rolling ? 'translateY(-100%)' : 'translateY(0)' }}
      >
        <span className="block h-[1.05em] leading-[1.05em]">{prev}</span>
        <span className="block h-[1.05em] leading-[1.05em]">{ch}</span>
      </span>
    </span>
  )
}

export function Odometer({ value, className = '' }: { value: string; className?: string }) {
  return (
    <span className={`tnum inline-flex items-baseline ${className}`}
          style={{ fontFamily: 'var(--font-display)' }}>
      {value.split('').map((ch, i) => <Digit key={i} ch={ch} />)}
    </span>
  )
}

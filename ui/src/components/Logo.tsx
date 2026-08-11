/** The wada-gate roundel, inline so it can take currentColor and scale crisply. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden>
      <circle cx="32" cy="32" r="30" fill="var(--color-paper)"
              stroke="var(--color-ink)" strokeWidth="2.5" />
      <path fill="var(--color-ink)"
            d="M13 50 V27 h6 v-4 h5 v4 h4 V19 h8 v8 h4 v-4 h5 v4 h6 v23 z" />
      <rect x="10" y="50" width="44" height="4" fill="var(--color-ink)" />
      <path fill="var(--color-haldi)"
            d="M32 25 c 6 0 9 4.5 9 10 c 0 6.5 -6 10.5 -9 15
               c -3 -4.5 -9 -8.5 -9 -15 c 0 -5.5 3 -10 9 -10 z" />
      <circle cx="32" cy="34" r="3.2" fill="var(--color-ink)" />
    </svg>
  )
}

/** Logo + wordmark, the top-left brand block. */
export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5 select-none">
      <Logo size={30} />
      <div className="leading-none">
        <div style={{ fontFamily: 'var(--font-display)' }} className="text-[19px]">
          पुणे <span className="tracking-[0.06em]">SIM</span>
        </div>
        <div className="survey text-[8.5px] text-[var(--color-ink-faint)] mt-[3px]">
          Old City · 18.52°N 73.86°E
        </div>
      </div>
    </div>
  )
}

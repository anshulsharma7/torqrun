/** Torqrun mark: a torque arc spinning around a solid core. */
export function LogoMark({ className = "size-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="tq-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffb22e" />
          <stop offset="0.5" stopColor="#ff6b2c" />
          <stop offset="1" stopColor="#ff3d81" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#tq-g)" />
      <path d="M9.5 18.5a6.5 6.5 0 1 1 9 6" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M17.2 21.6l1.8 3.4-3.7.9" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="2.4" fill="#fff" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-[-0.02em]">torqrun</span>
    </span>
  );
}

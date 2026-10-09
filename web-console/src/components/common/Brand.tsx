import { useId } from "react";

/** Shield + pulse mark. Pure SVG, themed through CSS variables. */
export function BrandMark({ size = 28, className }: { size?: number; className?: string }) {
  const g = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="ScamShield Immune" className={className}>
      <defs>
        <linearGradient id={g} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--intel-strong)" />
          <stop offset="1" stopColor="var(--flow)" />
        </linearGradient>
      </defs>
      <path
        d="M16 2.5 4.5 7v8.2c0 7 4.8 12.2 11.5 14.3 6.7-2.1 11.5-7.3 11.5-14.3V7z"
        fill="color-mix(in srgb, var(--intel-strong) 14%, var(--surface-solid))"
        stroke={`url(#${g})`}
        strokeWidth="2"
      />
      <path
        d="M8.5 16.5h4l2-5 3.2 9 2-4h3.8"
        fill="none"
        stroke="var(--safe)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function BrandWord({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark />
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight">
            ScamShield <span className="text-intel">Immune</span>
          </div>
          <div className="text-[11px] text-subtle">One bank&apos;s detection protects every bank</div>
        </div>
      )}
    </div>
  );
}

const PATHS = {
  overview: "M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z",
  stats: "M5 20v-7M12 20V5M19 20v-11",
  ads: "M4 5h16v14H4zM4 15l5-4 4 3 3-2 4 3",
  history: "M12 7v5l3 2M21 12a9 9 0 1 1-3-6.7M21 3v5h-5",
  chat: "M4 5h16v11H9l-5 4z",
  settings: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name }: { name: IconName }) {
  return (
    <svg className="ah-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[name]} /></svg>
  );
}

export function BrandLogo({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 512 512"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ verticalAlign: "middle", borderRadius: Math.round(size * 0.25) }}
    >
      <rect width="512" height="512" rx="130" fill="var(--accent, #2340d9)" />
      <g transform="translate(-14, -10)">
        <g fill="var(--on-accent, #ffffff)">
          <path d="M 164 196 C 146 196, 132 210, 132 228 L 132 284 C 132 302, 146 316, 164 316 L 182 316 L 282 374 C 292 380, 306 372, 306 360 L 306 152 C 306 140, 292 132, 282 138 L 182 196 Z" />
          <rect x="188" y="312" width="30" height="72" rx="15" />
        </g>
        <g fill="none" stroke="var(--on-accent, #ffffff)" strokeLinecap="round">
          <path d="M 346 202 A 76 76 0 0 1 346 310" strokeWidth="26" />
          <path d="M 392 164 A 136 136 0 0 1 392 348" strokeWidth="26" />
        </g>
      </g>
    </svg>
  );
}

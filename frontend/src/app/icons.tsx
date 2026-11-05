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

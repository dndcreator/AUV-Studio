const paths = {
  add: "M12 5v14M5 12h14",
  inspect: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm4.5-2 5 5M8 10.5h5M10.5 8v5",
  settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
  play: "m8 5 11 7-11 7V5Z",
  stop: "M6 6h12v12H6z",
  save: "M5 3h12l4 4v14H3V3h2Zm2 0v7h10V3M7 21v-7h10v7",
  folder: "M3 7V4h6l3 3h9v13H3V7Z",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "m15 4 5 5M4 20l5-1L21 7l-4-4L5 15l-1 5Z",
  person: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2",
  group: "M14 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM3 20v-2a7 7 0 0 1 14 0v2M17 4a3 3 0 0 1 0 6M18 14a6 6 0 0 1 4 6",
  organization: "M4 21V3h12v18M16 9h4v12M8 7h4M8 11h4M8 15h4M9 21v-2h2v2M2 21h20",
  world: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18Z",
  event: "m13 2-9 12h7l-1 8 10-13h-7l1-7Z",
  artifact: "m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 10 9-5M12 13 3 8m9 5v9M8 5l9 5",
  gate: "M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6",
  search: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Zm-2 5 6 6",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  close: "m6 6 12 12M6 18 18 6",
} as const;

export type StudioIconName = keyof typeof paths;

export function StudioIcon({ name }: { name: StudioIconName }) {
  return (
    <svg className="studio-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

import type { IconName } from "@/lib/present";

type Name =
  | IconName
  | "home"
  | "inbox"
  | "camera"
  | "users"
  | "log"
  | "sound"
  | "mute"
  | "sun"
  | "moon"
  | "auto"
  | "logout"
  | "live"
  | "unplug"
  | "lock"
  | "arrow-left"
  | "refresh";

const PATHS: Record<Name, React.ReactNode> = {
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  half: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" />
    </>
  ),
  cross: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  question: (
    <>
      <path d="M9.2 9a3 3 0 1 1 4.3 2.7c-.9.4-1.5 1.2-1.5 2.1v.7" />
      <path d="M12 18h.01" strokeWidth="3" />
    </>
  ),
  bang: (
    <>
      <path d="M12 5v9" />
      <path d="M12 18.5h.01" strokeWidth="3" />
    </>
  ),
  triangle: (
    <>
      <path d="M12 3.5L21.5 20h-19z" fill="currentColor" stroke="none" />
      <path d="M12 9.5v5" stroke="var(--triangle-mark, #fff)" />
      <path d="M12 17.3h.01" stroke="var(--triangle-mark, #fff)" strokeWidth="2.6" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="4" fill="currentColor" />,
  pause: <path d="M9 6v12M15 6v12" />,
  home: <path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" />,
  inbox: (
    <>
      <path d="M4 13l2.5-8h11L20 13v6H4z" />
      <path d="M4 13h4.5l1 2h5l1-2H20" />
    </>
  ),
  camera: (
    <>
      <rect x="3" y="7" width="13" height="10" rx="2" />
      <path d="M16 11l5-3v8l-5-3" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5" />
      <path d="M16 4.8a3.4 3.4 0 0 1 0 6.4M18.5 14.8c1.6.8 2.6 2.5 3 5.2" />
    </>
  ),
  log: (
    <>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
    </>
  ),
  sound: (
    <>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
    </>
  ),
  mute: (
    <>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path d="M16 9.5l5 5M21 9.5l-5 5" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </>
  ),
  moon: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" />,
  auto: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" />
    </>
  ),
  logout: (
    <>
      <path d="M10 4H5v16h5" />
      <path d="M14 8l4 4-4 4M18 12H9" />
    </>
  ),
  live: (
    <>
      <circle cx="12" cy="12" r="2.5" fill="currentColor" />
      <path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4" />
    </>
  ),
  unplug: (
    <>
      <path d="M4 20l4-4M20 4l-4 4" />
      <path d="M7 13l4 4 1.5-1.5a3 3 0 0 0 0-4.2l-0.8-.8a3 3 0 0 0-4.2 0z" />
      <path d="M17 11l-4-4-1.5 1.5" />
      <path d="M3 3l18 18" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
    </>
  ),
  "arrow-left": <path d="M19 12H5M11 6l-6 6 6 6" />,
  refresh: (
    <>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.5 4.5v4h-4" />
    </>
  ),
};

export function Icon({ name, size = 20, className, title }: { name: Name; size?: number; className?: string; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      {PATHS[name]}
    </svg>
  );
}

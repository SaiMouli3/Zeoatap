"use client";

import { useSyncExternalStore } from "react";
import { Icon } from "./Icon";

type Mode = "system" | "light" | "dark";
const KEY = "zeoatap_theme";
const NEXT: Record<Mode, Mode> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Mode, string> = { system: "Theme: automatic", light: "Theme: light", dark: "Theme: dark" };

function read(): Mode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(mode: Mode) {
  const el = document.documentElement;
  if (mode === "system") delete el.dataset.theme;
  else el.dataset.theme = mode;
}

/** Inline script for <head>: applies the stored theme before first paint. */
export const THEME_BOOT = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

const listeners = new Set<() => void>();
function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function ThemeToggle() {
  const mode = useSyncExternalStore(subscribe, read, () => "system" as Mode);
  const next = () => {
    const m = NEXT[mode];
    applyTheme(m);
    try {
      if (m === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, m);
    } catch {
      /* storage unavailable: theme still applies for this page */
    }
    listeners.forEach((l) => l());
  };
  return (
    <button type="button" className="icon-btn" onClick={next} aria-label={`${LABEL[mode]}. Change theme`} title={LABEL[mode]}>
      <Icon name={mode === "light" ? "sun" : mode === "dark" ? "moon" : "auto"} />
    </button>
  );
}

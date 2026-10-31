// Remembers (per tab) that this tab had a session, so a later 401 can be
// explained as "your session expired" rather than a plain sign-in prompt.
const FLAG = "zeoatap_signed_in";

export function markSignedIn(v: boolean) {
  try {
    if (v) sessionStorage.setItem(FLAG, "1");
    else sessionStorage.removeItem(FLAG);
  } catch {
    /* storage unavailable */
  }
}

export function wasSignedIn(): boolean {
  try {
    return sessionStorage.getItem(FLAG) === "1";
  } catch {
    return false;
  }
}

/** Only allow same-site relative paths as post-login destinations. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/login")) return "/";
  return next;
}

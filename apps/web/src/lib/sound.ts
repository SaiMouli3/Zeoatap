// Alert chime generated with WebAudio (no audio files). Browsers block audio
// until a user gesture, so the context is created from the "Enable alert sound"
// button and reused for the lifetime of the page.

type AudioCtor = typeof AudioContext;

let ctx: AudioContext | null = null;

export function soundSupported(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as unknown as { AudioContext?: AudioCtor; webkitAudioContext?: AudioCtor };
  return !!(w.AudioContext ?? w.webkitAudioContext);
}

/** Must be called from a user gesture. Returns true once audio is allowed. */
export async function enableSound(): Promise<boolean> {
  if (!soundSupported()) return false;
  const w = window as unknown as { AudioContext?: AudioCtor; webkitAudioContext?: AudioCtor };
  const Ctor = (w.AudioContext ?? w.webkitAudioContext) as AudioCtor;
  ctx ??= new Ctor();
  try {
    await ctx.resume();
  } catch {
    return false;
  }
  playChime(true);
  return ctx.state === "running";
}

export function disableSound() {
  void ctx?.suspend();
}

/** Two soft tones (E5 → A5), ~0.6 s. `quiet` for the confirmation blip. */
export function playChime(quiet = false) {
  if (!ctx || ctx.state !== "running") return;
  const now = ctx.currentTime;
  const peak = quiet ? 0.08 : 0.25;
  [659.25, 880].forEach((freq, i) => {
    const t0 = now + i * 0.18;
    const osc = ctx!.createOscillator();
    const gain = ctx!.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.4);
    osc.connect(gain).connect(ctx!.destination);
    osc.start(t0);
    osc.stop(t0 + 0.45);
  });
}

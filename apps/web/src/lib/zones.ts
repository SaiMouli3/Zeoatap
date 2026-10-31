import type { Zone } from "./types";

export const ZONE_KINDS = ["surface", "restricted", "sleep"] as const;

export type ZoneValidation = { ok: true; zones: Zone[] } | { ok: false; errors: string[] };

/** Validates the zones JSON typed by an admin. Coordinates are normalised (0..1). */
export function validateZonesJson(text: string): ZoneValidation {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, zones: [] };
  let data: unknown;
  try {
    data = JSON.parse(trimmed);
  } catch (e) {
    return { ok: false, errors: [`Not valid JSON: ${e instanceof Error ? e.message : String(e)}`] };
  }
  if (!Array.isArray(data)) return { ok: false, errors: ["Zones must be a JSON array, e.g. [{\"id\": \"table\", ...}]."] };
  const errors: string[] = [];
  const ids = new Set<string>();
  data.forEach((z: unknown, i: number) => {
    const where = `Zone ${i + 1}`;
    if (typeof z !== "object" || z === null || Array.isArray(z)) {
      errors.push(`${where}: must be an object.`);
      return;
    }
    const o = z as Record<string, unknown>;
    if (typeof o.id !== "string" || o.id.trim() === "") errors.push(`${where}: "id" must be a non-empty string.`);
    else if (ids.has(o.id)) errors.push(`${where}: duplicate id "${o.id}".`);
    else ids.add(o.id);
    if (typeof o.room_zone !== "string" || o.room_zone.trim() === "") errors.push(`${where}: "room_zone" must be a non-empty string (the room-level zone this maps to).`);
    if (typeof o.kind !== "string" || !(ZONE_KINDS as readonly string[]).includes(o.kind)) errors.push(`${where}: "kind" must be one of ${ZONE_KINDS.join(", ")}.`);
    if (!Array.isArray(o.polygon) || o.polygon.length < 3) {
      errors.push(`${where}: "polygon" needs at least 3 points.`);
    } else {
      o.polygon.forEach((p: unknown, j: number) => {
        const q = p as Record<string, unknown> | null;
        const ok =
          q !== null && typeof q === "object" && typeof q.X === "number" && typeof q.Y === "number" && q.X >= 0 && q.X <= 1 && q.Y >= 0 && q.Y <= 1;
        if (!ok) errors.push(`${where}, point ${j + 1}: must be {"X": n, "Y": n} with values between 0 and 1.`);
      });
    }
  });
  if (errors.length) return { ok: false, errors };
  return { ok: true, zones: data as Zone[] };
}

export const ZONES_EXAMPLE = JSON.stringify(
  [
    {
      id: "table",
      room_zone: "changing-table-1",
      kind: "surface",
      polygon: [
        { X: 0.1, Y: 0.3 },
        { X: 0.6, Y: 0.3 },
        { X: 0.6, Y: 0.7 },
        { X: 0.1, Y: 0.7 },
      ],
    },
  ],
  null,
  2,
);

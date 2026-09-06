// Taste compatibility between two listeners' top-artist lists.
// Score = 100 * (shared listening weight) / (combined listening weight),
// where each shared artist contributes min/max similarity weighted by volume.
export interface TasteArtist {
  name: string;
  playcount?: number | string;
}

export interface SharedArtist {
  name: string;
  mine: number;
  theirs: number;
  combined: number;
}

export interface TasteResult {
  score: number;
  shared: SharedArtist[];
}

export function tasteMatch(mine: TasteArtist[] = [], theirs: TasteArtist[] = []): TasteResult {
  const num = (v: unknown) => {
    const n = Number(v || 0);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };
  const a = new Map<string, { name: string; plays: number }>();
  for (const t of mine) {
    const key = (t.name || "").toLowerCase().trim();
    if (!key || a.has(key)) continue;
    a.set(key, { name: t.name, plays: num(t.playcount) });
  }
  const b = new Map<string, { name: string; plays: number }>();
  for (const t of theirs) {
    const key = (t.name || "").toLowerCase().trim();
    if (!key || b.has(key)) continue;
    b.set(key, { name: t.name, plays: num(t.playcount) });
  }

  let sharedWeight = 0;
  let totalWeight = 0;
  const shared: SharedArtist[] = [];
  const seen = new Set<string>();

  for (const [key, x] of a) {
    const y = b.get(key);
    const w = x.plays + (y?.plays || 0);
    totalWeight += w;
    seen.add(key);
    if (y) {
      const mx = Math.max(x.plays, y.plays);
      const sim = mx > 0 ? Math.min(x.plays, y.plays) / mx : 0;
      sharedWeight += w * sim;
      shared.push({ name: x.name, mine: x.plays, theirs: y.plays, combined: w });
    }
  }
  for (const [key, y] of b) {
    if (!seen.has(key)) totalWeight += y.plays;
  }

  shared.sort((x, y) => y.combined - x.combined);
  const score = totalWeight > 0 ? Math.round((sharedWeight / totalWeight) * 100) : 0;
  return { score, shared: shared.slice(0, 10) };
}

export function tasteLabel(score: number): string {
  if (score >= 80) return "Musical soulmates";
  if (score >= 60) return "Strong overlap";
  if (score >= 40) return "Shared wavelength";
  if (score >= 20) return "Some common ground";
  if (score >= 1) return "Mostly different worlds";
  return "No overlap yet";
}

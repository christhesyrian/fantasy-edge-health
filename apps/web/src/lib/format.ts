/** Display formatting. Kept in one place so a table never invents its own. */

/** Fixed-decimal number, or an em dash when a value is genuinely absent. */
export function num(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

/** Whole number, or an em dash. */
export function int(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : Math.round(value).toString();
}

/** Percentage from a 0-1 fraction. */
export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;
}

/** Signed number, so a positive delta always reads as a gain. */
export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  const formatted = value.toFixed(digits);
  return value > 0 ? `+${formatted}` : formatted;
}

/** Turn an enum token into a readable label. */
export function humanise(token: string): string {
  return token
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * Severity glyph for an availability-risk band.
 *
 * Risk is deliberately encoded by shape as well as colour, so the signal
 * survives greyscale, colour-vision differences, and a glance at arm's length.
 */
export function riskGlyph(band: string): string {
  switch (band) {
    case "SEVERE":
      return "▇▇▇▇";
    case "ELEVATED":
      return "▇▇▇░";
    case "MODERATE":
      return "▇▇░░";
    default:
      return "▇░░░";
  }
}

/** Relative time, for freshness stamps. */
export function since(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

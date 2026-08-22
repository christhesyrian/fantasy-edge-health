import { cn } from "@/lib/cn";
import { riskGlyph } from "@/lib/format";

const BAND_STYLE: Record<string, string> = {
  SEVERE: "text-[var(--color-hazard-400)]",
  ELEVATED: "text-[var(--color-warn-400)]",
  MODERATE: "text-[var(--color-hold-400)]",
  LOW: "text-[var(--color-go-400)]",
};

/**
 * Availability risk, encoded three ways at once.
 *
 * The glyph carries the severity in shape, the label carries it in words, and
 * colour is the third channel rather than the only one. A manager scanning at
 * arm's length, a colour-blind manager, and a greyscale screenshot all get the
 * same information.
 */
export function RiskBadge({
  score,
  band,
  showScore = true,
  className,
}: {
  score: number | null | undefined;
  band: string | null | undefined;
  showScore?: boolean;
  className?: string;
}) {
  if (score === null || score === undefined || !band) {
    return (
      <span className={cn("tabular text-[var(--text-muted)]", className)}>
        <span aria-hidden>░░░░</span>
        <span className="sr-only">availability risk unknown</span>
      </span>
    );
  }

  return (
    <span
      className={cn("inline-flex items-center gap-1.5", BAND_STYLE[band], className)}
      title={`Availability risk ${Math.round(score)}/100 — ${band.toLowerCase()}`}
    >
      <span aria-hidden className="text-[0.7rem] leading-none tracking-[-0.1em]">
        {riskGlyph(band)}
      </span>
      {showScore ? (
        <span className="tabular text-[0.8125rem]">{Math.round(score)}</span>
      ) : null}
      <span className="sr-only">
        availability risk {Math.round(score)} out of 100, {band.toLowerCase()}
      </span>
    </span>
  );
}

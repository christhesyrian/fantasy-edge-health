import { cn } from "@/lib/cn";

/**
 * A control-room panel: a labelled surface with a hairline border and a
 * broadcast-style header rail.
 */
export function Panel({
  title,
  meta,
  actions,
  children,
  className,
  bodyClassName,
  accent = false,
  dense = false,
}: {
  title: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  accent?: boolean;
  dense?: boolean;
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 flex-col border bg-[var(--surface-panel)]",
        accent && "border-[var(--hairline-bright)]",
        className,
      )}
    >
      <header
        className={cn(
          "flex shrink-0 items-center justify-between gap-3 border-b px-3",
          dense ? "py-1" : "py-2",
          accent && "bg-[color-mix(in_oklab,var(--accent)_9%,transparent)]",
        )}
      >
        <div className="flex items-baseline gap-2.5">
          <h2 className="rail-label text-[var(--text-secondary)]">{title}</h2>
          {meta ? (
            <span className="tabular text-[0.6875rem] text-[var(--text-muted)]">
              {meta}
            </span>
          ) : null}
        </div>
        {actions}
      </header>
      <div className={cn("min-h-0 flex-1 overflow-auto", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}

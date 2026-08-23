"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import type { Recommendation } from "@/lib/types";

/**
 * Command palette.
 *
 * Opened with ⌘K or Ctrl+K. Exists because the alternative under a draft clock
 * is hunting for a control with a mouse, and the two things a manager actually
 * wants — "find this player" and "show me only receivers" — are both one phrase
 * away from being typed.
 *
 * Deliberately not a fuzzy matcher: with a hundred names on screen a substring
 * match is predictable, and predictability beats cleverness when you have ten
 * seconds.
 */

export interface Command {
  id: string;
  label: string;
  hint?: string;
  keywords?: string;
  run: () => void;
}

const MAX_RESULTS = 8;

interface PaletteProps {
  open: boolean;
  onClose: () => void;
  commands: Command[];
  players: Recommendation[];
  onSelectPlayer: (playerUuid: string) => void;
}

/**
 * Mounting the body only while open is what resets the query and the highlight.
 * Clearing them from an effect would mean rendering the stale pair once first,
 * and would re-run on every prop change rather than only on open.
 */
export function CommandPalette(props: PaletteProps) {
  if (!props.open) return null;
  return <PaletteBody {...props} />;
}

function PaletteBody({ onClose, commands, players, onSelectPlayer }: PaletteProps) {
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const entries = useMemo<Command[]>(() => {
    const needle = query.trim().toLowerCase();

    const matchedCommands = commands.filter(
      (command) =>
        needle === "" ||
        command.label.toLowerCase().includes(needle) ||
        (command.keywords ?? "").toLowerCase().includes(needle),
    );

    // Players only appear once something is typed. Listing four hundred names
    // by default would bury the commands.
    const matchedPlayers: Command[] =
      needle.length >= 2
        ? players
            .filter(
              (player) =>
                player.name.toLowerCase().includes(needle) ||
                (player.team ?? "").toLowerCase().includes(needle),
            )
            .slice(0, MAX_RESULTS)
            .map((player) => ({
              id: `player:${player.player_uuid}`,
              label: player.name,
              hint: `${player.position} · ${player.team ?? "FA"} · rank ${player.model_rank}`,
              run: () => onSelectPlayer(player.player_uuid),
            }))
        : [];

    return [...matchedCommands.slice(0, MAX_RESULTS), ...matchedPlayers];
  }, [query, commands, players, onSelectPlayer]);

  // Typing shrinks the list, which can strand the highlight past the end.
  // Clamping on read keeps the rendered selection valid without a correcting
  // render, so Enter can never fire a command that is no longer on screen.
  const active = entries.length === 0 ? 0 : Math.min(highlighted, entries.length - 1);

  const run = (command: Command | undefined) => {
    if (!command) return;
    command.run();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh]">
      <button
        type="button"
        aria-label="Close command palette"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="reticle relative w-full max-w-xl border border-[var(--hairline-bright)] bg-[var(--surface-panel)] shadow-2xl"
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setHighlighted((active + 1) % Math.max(1, entries.length));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setHighlighted(
                (active - 1 + entries.length) % Math.max(1, entries.length),
              );
            }
            if (event.key === "Enter") {
              event.preventDefault();
              run(entries[active]);
            }
          }}
          placeholder="Search players, or type a command…"
          aria-label="Search players or commands"
          className="w-full border-b bg-transparent px-4 py-3 text-[0.95rem] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none"
        />

        <ul className="max-h-[50vh] overflow-auto py-1" role="listbox">
          {entries.map((entry, index) => (
            <li key={entry.id} role="option" aria-selected={index === active}>
              <button
                type="button"
                onMouseEnter={() => setHighlighted(index)}
                onClick={() => run(entry)}
                className={cn(
                  "flex w-full items-baseline justify-between gap-4 px-4 py-2 text-left",
                  index === active
                    ? "bg-[color-mix(in_oklab,var(--accent)_16%,transparent)] text-[var(--accent)]"
                    : "text-[var(--text-primary)]",
                )}
              >
                <span className="truncate text-[0.875rem]">{entry.label}</span>
                {entry.hint ? (
                  <span className="tabular shrink-0 text-[0.7rem] text-[var(--text-muted)]">
                    {entry.hint}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
          {entries.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
              Nothing matches “{query}”.
            </li>
          ) : null}
        </ul>

        <div className="flex items-center gap-3 border-t px-4 py-1.5">
          <span className="rail-label">↑↓ move</span>
          <span className="rail-label">⏎ run</span>
          <span className="rail-label">esc close</span>
        </div>
      </div>
    </div>
  );
}

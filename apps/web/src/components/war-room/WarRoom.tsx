"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Panel } from "@/components/ui/Panel";
import { AlertRail } from "@/components/war-room/AlertRail";
import { BestAvailable, type Filter } from "@/components/war-room/BestAvailable";
import { CommandPalette, type Command } from "@/components/war-room/CommandPalette";
import { CompareTray } from "@/components/war-room/CompareTray";
import { DraftTicker } from "@/components/war-room/DraftTicker";
import { HeadlinePicks } from "@/components/war-room/HeadlinePicks";
import { MyRoster } from "@/components/war-room/MyRoster";
import { PlayerDrawer } from "@/components/war-room/PlayerDrawer";
import { RecommendationPanel } from "@/components/war-room/RecommendationPanel";
import { ScarcityStrip } from "@/components/war-room/ScarcityStrip";
import { TopRail } from "@/components/war-room/TopRail";
import { api, ApiError } from "@/lib/api";
import { useDraftStream } from "@/lib/useDraftStream";
import { PREVIEW_DRAFT_REFUSED, PREVIEW_MODE } from "@/lib/preview/mode";
import { useFavourites } from "@/lib/useFavourites";
import { useTheme } from "@/lib/useTheme";

const MAX_COMPARE = 4;

/**
 * The war room.
 *
 * State discipline: the board is *always* read from the server. Events never
 * mutate a local copy of it. That is deliberate — an optimistically-patched
 * board that drifts from the engine's answer is worse than a board that lags by
 * 200ms, because the manager cannot tell which one they are looking at.
 */
/**
 * Palette wording for each board filter. Written out rather than derived from
 * the filter keys, because the derived form reads "Show te" — the palette is
 * something you scan under a clock, so it gets real words.
 */
const FILTER_COMMANDS: { key: Filter; label: string }[] = [
  { key: "ALL", label: "Show all players" },
  { key: "QB", label: "Show quarterbacks" },
  { key: "RB", label: "Show running backs" },
  { key: "WR", label: "Show wide receivers" },
  { key: "TE", label: "Show tight ends" },
  { key: "K", label: "Show kickers" },
  { key: "DEF", label: "Show defences" },
  { key: "FLEX", label: "Show flex-eligible players" },
  { key: "VALUE", label: "Show value picks" },
  { key: "HEALTHY", label: "Show healthy players" },
  { key: "FAVOURITES", label: "Show favourites" },
];

export function WarRoom({ simulationId }: { simulationId: string }) {
  const queryClient = useQueryClient();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [inspectUuid, setInspectUuid] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const favourites = useFavourites();
  const { theme, cycle: cycleTheme } = useTheme();

  const boardQuery = useQuery({
    queryKey: ["board", simulationId],
    queryFn: () => api.getBoard(simulationId, 150),
    refetchOnWindowFocus: true,
  });

  // Simulator-only actions are gated on this. Defaults to true so the very
  // first render of a demo behaves normally; a live board flips it on arrival.
  const isDemo = boardQuery.data?.is_demo ?? true;

  const refetchBoard = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["board", simulationId] });
  }, [queryClient, simulationId]);

  const stream = useDraftStream(api.eventStreamUrl(simulationId), {
    onEvent: (event) => {
      // Any state-changing event triggers a canonical re-read rather than a
      // local patch, so what is on screen is always what the engine computed.
      if (event.type === "board_updated" || event.type === "pick_made") {
        refetchBoard();
      }
    },
    onResyncRequired: (reason) => {
      setNotice(`Re-syncing: ${reason}`);
      refetchBoard();
      window.setTimeout(() => setNotice(null), 4000);
    },
  });

  const advance = useMutation({
    mutationFn: (picks: number) => api.advance(simulationId, picks, true),
    onSuccess: refetchBoard,
    onError: (error: ApiError) => setNotice(error.message),
  });

  const draft = useMutation({
    mutationFn: (playerUuid: string) => api.pick(simulationId, playerUuid),
    onSuccess: () => {
      setSelectedUuid(null);
      refetchBoard();
    },
    onError: (error: ApiError) => setNotice(error.message),
  });

  const reset = useMutation({
    mutationFn: () => api.reset(simulationId),
    onSuccess: refetchBoard,
  });

  const onDraft = useCallback(
    (uuid: string) => {
      if (PREVIEW_MODE) {
        // The recording holds the picks the simulator actually made, so
        // honouring an arbitrary choice would show a different player as
        // drafted than the one that was clicked.
        setNotice(PREVIEW_DRAFT_REFUSED);
        window.setTimeout(() => setNotice(null), 5000);
        return;
      }
      draft.mutate(uuid);
    },
    [draft],
  );

  const toggleCompare = useCallback((uuid: string) => {
    setComparing((current) =>
      current.includes(uuid)
        ? current.filter((entry) => entry !== uuid)
        : current.length >= MAX_COMPARE
          ? current
          : [...current, uuid],
    );
  }, []);

  // Keyboard shortcuts. Deliberately single keys with no modifier conflicts:
  // hands stay on the board, not the mouse, while the clock runs.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);

      // The palette is the one shortcut that works while typing, because it is
      // how you get *out* of a field and into a command.
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (typing) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "f" && selectedUuid) favourites.toggle(selectedUuid);

      // Advancing and drafting are simulator actions. On a live draft the room
      // makes the picks, so these keys do nothing rather than 409.
      if (isDemo && event.key === "n") advance.mutate(1);
      if (isDemo && event.key === "a") advance.mutate(500);
      if (isDemo && !PREVIEW_MODE && event.key === "Enter" && selectedUuid) {
        draft.mutate(selectedUuid);
      }
      if (event.key === "i" && selectedUuid) setInspectUuid(selectedUuid);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, draft, selectedUuid, isDemo, favourites]);

  // Built here rather than inside the palette so every command closes over the
  // same state the rest of the screen is reading.
  const commands: Command[] = [
    ...FILTER_COMMANDS.map(({ key, label }) => ({
      id: `filter:${key}`,
      label,
      keywords: `filter position ${key}`,
      run: () => setFilter(key),
    })),
    {
      id: "favourite",
      label: "Toggle favourite on the selected player",
      hint: "f",
      keywords: "star watch",
      run: () => selectedUuid && favourites.toggle(selectedUuid),
    },
    {
      id: "inspect",
      label: "Open the selected player",
      hint: "i",
      keywords: "detail drawer health",
      run: () => selectedUuid && setInspectUuid(selectedUuid),
    },
    {
      id: "compare",
      label: "Add the selected player to the comparison",
      keywords: "versus vs",
      run: () => selectedUuid && toggleCompare(selectedUuid),
    },
    {
      id: "clear-compare",
      label: "Clear the comparison",
      keywords: "versus vs reset",
      run: () => setComparing([]),
    },
    {
      id: "theme",
      label: "Cycle theme: dark, light, system",
      keywords: "dark light appearance",
      run: cycleTheme,
    },
    ...(isDemo
      ? [
          {
            id: "advance",
            label: "Advance one pick",
            hint: "n",
            keywords: "next simulate",
            run: () => advance.mutate(1),
          },
          {
            id: "advance-to-turn",
            label: "Run to my next pick",
            hint: "a",
            keywords: "fast forward simulate",
            run: () => advance.mutate(500),
          },
          {
            id: "reset",
            label: "Reset the draft",
            keywords: "restart simulate",
            run: () => reset.mutate(),
          },
        ]
      : []),
  ];

  if (boardQuery.isLoading) {
    return (
      <main className="flex h-dvh items-center justify-center">
        <div className="text-center">
          <div className="display animate-pulse text-2xl text-[var(--accent)]">
            Loading war room
          </div>
          <p className="rail-label mt-2">Building the board</p>
        </div>
      </main>
    );
  }

  if (boardQuery.isError || !boardQuery.data) {
    const error = boardQuery.error;
    return (
      <main className="flex h-dvh items-center justify-center p-8">
        <div className="max-w-md border border-[var(--color-hazard-500)] bg-[var(--surface-panel)] p-6">
          <h1 className="display text-xl text-[var(--color-hazard-400)]">
            Could not load this draft
          </h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {error instanceof ApiError ? error.message : "The API is unreachable."}
          </p>
          <p className="mt-3 text-[0.75rem] text-[var(--text-muted)]">
            Sessions live in memory and do not survive an API restart. Start a new draft
            from the home page.
          </p>
          <Link
            href="/"
            className="display mt-4 inline-block border border-[var(--accent)] px-3 py-1.5 text-sm text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--color-pit-000)]"
          >
            Start over
          </Link>
        </div>
      </main>
    );
  }

  const board = boardQuery.data;
  const selected =
    board.recommendations.find((row) => row.player_uuid === selectedUuid) ?? null;
  const focus = selected ?? board.best_pick ?? null;

  return (
    <main className="flex h-dvh flex-col overflow-hidden">
      <TopRail
        board={board}
        connection={PREVIEW_MODE ? "PREVIEW" : stream.connection}
        lastEventAt={PREVIEW_MODE ? null : stream.lastEventAt}
      />

      {notice ? (
        <div
          role="status"
          className="shrink-0 border-b border-[var(--color-hold-400)] bg-[color-mix(in_oklab,var(--color-hold-400)_12%,transparent)] px-4 py-1.5 text-[0.8125rem] text-[var(--color-hold-400)]"
        >
          {notice}
        </div>
      ) : null}

      {board.warnings.length > 0 ? (
        <details className="shrink-0 border-b border-[var(--color-hold-400)] bg-[color-mix(in_oklab,var(--color-hold-400)_9%,transparent)] px-4 py-1.5">
          <summary className="rail-label cursor-pointer text-[var(--color-hold-400)]">
            This board is missing data · {board.warnings.length}
          </summary>
          <ul className="mt-1.5 space-y-0.5 pb-1">
            {board.warnings.map((warning) => (
              <li
                key={warning}
                className="text-[0.75rem] leading-snug text-[var(--text-secondary)]"
              >
                — {warning}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="shrink-0 border-b">
        <AlertRail alerts={board.alerts} onFocusPlayer={setSelectedUuid} />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.35fr)_minmax(360px,1fr)_minmax(248px,0.55fr)] gap-2 p-2">
        <Panel
          title="Best available"
          meta={`${board.recommendations.length} on the board`}
          className="min-h-0"
          bodyClassName="p-0"
          actions={
            board.is_demo ? (
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => advance.mutate(1)}
                  disabled={advance.isPending || board.status === "complete"}
                  className="rail-label border px-2 py-1 transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-40"
                  title="Advance one pick (n)"
                >
                  Next ·&nbsp;n
                </button>
                <button
                  type="button"
                  onClick={() => advance.mutate(500)}
                  disabled={advance.isPending || board.is_user_on_the_clock}
                  className="rail-label border px-2 py-1 transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-40"
                  title="Run to your next pick (a)"
                >
                  To my pick ·&nbsp;a
                </button>
                <button
                  type="button"
                  onClick={() => reset.mutate()}
                  className="rail-label border px-2 py-1 transition-colors hover:border-[var(--color-hazard-400)] hover:text-[var(--color-hazard-400)]"
                >
                  Reset
                </button>
              </div>
            ) : (
              // A live draft advances because the room picks, not because we
              // ask it to. Showing controls that cannot work would be a lie.
              <span className="rail-label text-[var(--color-go-400)]">
                Following Sleeper
              </span>
            )
          }
        >
          <BestAvailable
            rows={board.recommendations}
            selectedUuid={selectedUuid}
            comparing={comparing}
            onSelect={setSelectedUuid}
            onInspect={setInspectUuid}
            onToggleCompare={toggleCompare}
            onDraft={onDraft}
            isOnTheClock={board.is_user_on_the_clock}
            favourites={favourites.ids}
            onToggleFavourite={favourites.toggle}
            filter={filter}
            onFilterChange={setFilter}
          />
        </Panel>

        <div className="flex min-h-0 flex-col gap-2">
          <Panel
            title={selected ? "Selected player" : "Best pick right now"}
            meta={
              board.computation_ms ? `computed in ${board.computation_ms}ms` : undefined
            }
            accent={board.is_user_on_the_clock}
            className="min-h-0 flex-1"
            bodyClassName="p-0"
          >
            <RecommendationPanel
              pick={focus}
              isOnTheClock={board.is_user_on_the_clock}
              onDraft={onDraft}
              onInspect={setInspectUuid}
              isDrafting={draft.isPending}
            />
          </Panel>

          <Panel title="Alternatives" dense className="shrink-0" bodyClassName="p-0">
            <HeadlinePicks
              board={board}
              onInspect={setInspectUuid}
              onSelect={setSelectedUuid}
            />
          </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-2">
          <Panel
            title="My roster"
            meta={
              board.my_roster
                ? `${board.my_roster.unfilled_starting_slots.length} unfilled`
                : undefined
            }
            className="min-h-0 flex-[1.15]"
            bodyClassName="p-0"
          >
            <MyRoster roster={board.my_roster} onInspect={setInspectUuid} />
          </Panel>

          <Panel title="Scarcity" className="min-h-0 flex-1" bodyClassName="p-0">
            <ScarcityStrip scarcity={board.scarcity} />
          </Panel>

          <Panel title="Draft ticker" className="min-h-0 flex-1" bodyClassName="p-0">
            <DraftTicker
              picks={board.recent_picks}
              userDraftSlot={board.league.user_draft_slot}
            />
          </Panel>
        </div>
      </div>

      <CompareTray
        simulationId={simulationId}
        playerUuids={comparing}
        onClear={() => setComparing([])}
        onRemove={(uuid) =>
          setComparing((current) => current.filter((entry) => entry !== uuid))
        }
        onInspect={setInspectUuid}
      />

      <footer className="flex shrink-0 items-center gap-4 border-t bg-[var(--surface-panel)] px-4 py-1.5">
        <span className="rail-label">
          ⌘K commands · n next · a to my pick · enter draft · f favourite · i inspect
        </span>
        <button
          type="button"
          onClick={cycleTheme}
          className="rail-label transition-colors hover:text-[var(--accent)]"
          title="Cycle dark, light, and system themes"
        >
          theme: {theme}
        </button>
        <span className="rail-label ml-auto">
          {board.provenance.map((entry) => entry.source).join(" · ") ||
            "no projection or ADP source"}
        </span>
      </footer>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
        players={board.recommendations}
        onSelectPlayer={setSelectedUuid}
      />

      <PlayerDrawer
        simulationId={simulationId}
        playerUuid={inspectUuid}
        onClose={() => setInspectUuid(null)}
      />
    </main>
  );
}

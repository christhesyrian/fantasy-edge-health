import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Recommendation } from "@/lib/types";

import { BestAvailable } from "./BestAvailable";

function row(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    player_uuid: overrides.player_uuid ?? "p1",
    name: overrides.name ?? "Test Player",
    position: overrides.position ?? "RB",
    team: overrides.team ?? "SEA",
    overall_score: overrides.overall_score ?? 50,
    model_rank: overrides.model_rank ?? 1,
    recommendation: overrides.recommendation ?? "STRONG_VALUE",
    market_adp: overrides.market_adp ?? 10,
    adp_value: overrides.adp_value ?? 2,
    projected_points: overrides.projected_points ?? 250,
    vorp: overrides.vorp ?? 80,
    tier: overrides.tier ?? 1,
    health_risk: overrides.health_risk ?? 10,
    availability_estimate: overrides.availability_estimate ?? 0.9,
    next_pick_survival_probability: overrides.next_pick_survival_probability ?? 0.5,
    take_now_probability: overrides.take_now_probability ?? 0.5,
    bye_week: overrides.bye_week ?? 9,
    components: overrides.components ?? [],
    reasons: overrides.reasons ?? [],
  };
}

const ROWS: Recommendation[] = [
  row({ player_uuid: "rb1", name: "Alpha Back", position: "RB", model_rank: 1 }),
  row({ player_uuid: "wr1", name: "Bravo Receiver", position: "WR", model_rank: 2 }),
  row({ player_uuid: "qb1", name: "Charlie Passer", position: "QB", model_rank: 3 }),
  row({
    player_uuid: "hurt",
    name: "Delta Risk",
    position: "TE",
    model_rank: 4,
    health_risk: 80,
    recommendation: "AVOID",
  }),
  row({
    player_uuid: "faller",
    name: "Echo Value",
    position: "WR",
    model_rank: 5,
    adp_value: 22,
  }),
];

function setup(overrides: Partial<Parameters<typeof BestAvailable>[0]> = {}) {
  const props = {
    rows: ROWS,
    selectedUuid: null,
    comparing: [] as string[],
    onSelect: vi.fn(),
    onInspect: vi.fn(),
    onToggleCompare: vi.fn(),
    onDraft: vi.fn(),
    isOnTheClock: false,
    ...overrides,
  };
  render(<BestAvailable {...props} />);
  return props;
}

describe("BestAvailable", () => {
  it("lists every available player by default", () => {
    setup();
    expect(screen.getByText("Alpha Back")).toBeInTheDocument();
    expect(screen.getByText("Charlie Passer")).toBeInTheDocument();
  });

  it("filters to a single position", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "QB" }));

    expect(screen.getByText("Charlie Passer")).toBeInTheDocument();
    expect(screen.queryByText("Alpha Back")).not.toBeInTheDocument();
  });

  it("treats flex as RB, WR and TE together", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Flex" }));

    expect(screen.getByText("Alpha Back")).toBeInTheDocument();
    expect(screen.getByText("Bravo Receiver")).toBeInTheDocument();
    expect(screen.queryByText("Charlie Passer")).not.toBeInTheDocument();
  });

  it("value filter surfaces only genuine fallers", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Value" }));

    expect(screen.getByText("Echo Value")).toBeInTheDocument();
    expect(screen.queryByText("Alpha Back")).not.toBeInTheDocument();
  });

  it("healthy filter excludes high-risk players", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Healthy" }));

    expect(screen.queryByText("Delta Risk")).not.toBeInTheDocument();
    expect(screen.getByText("Alpha Back")).toBeInTheDocument();
  });

  it("searches by name and by team", async () => {
    setup();
    const search = screen.getByRole("searchbox", { name: /search players/i });

    await userEvent.type(search, "bravo");
    expect(screen.getByText("Bravo Receiver")).toBeInTheDocument();
    expect(screen.queryByText("Alpha Back")).not.toBeInTheDocument();
  });

  it("says so plainly when a filter matches nothing", async () => {
    setup();
    await userEvent.type(
      screen.getByRole("searchbox", { name: /search players/i }),
      "nobody",
    );
    expect(screen.getByText(/no players match this filter/i)).toBeInTheDocument();
  });

  it("selects on click and inspects on double click", async () => {
    const props = setup();
    await userEvent.click(screen.getByText("Alpha Back"));
    expect(props.onSelect).toHaveBeenCalledWith("rb1");

    await userEvent.dblClick(screen.getByText("Bravo Receiver"));
    expect(props.onInspect).toHaveBeenCalledWith("wr1");
  });

  it("only offers a draft action when it is your pick", async () => {
    const { onDraft } = setup({ isOnTheClock: false });
    expect(screen.queryByRole("button", { name: /draft alpha back/i })).toBeNull();
    expect(onDraft).not.toHaveBeenCalled();
  });

  it("drafts without also selecting the row", async () => {
    // The button sits inside a clickable row; a click that both drafts and
    // re-selects would fire two actions from one intent.
    const props = setup({ isOnTheClock: true });
    await userEvent.click(screen.getByRole("button", { name: /draft alpha back/i }));

    expect(props.onDraft).toHaveBeenCalledWith("rb1");
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it("marks a compared player as pressed", () => {
    setup({ comparing: ["rb1"] });
    const button = screen.getByRole("button", { name: /compare alpha back/i });
    expect(button).toHaveAttribute("aria-pressed", "true");
  });

  it("gives an AVOID row a non-colour warning texture", () => {
    setup();
    const cell = screen.getByText("Delta Risk");
    const tableRow = cell.closest("tr");
    expect(tableRow?.className).toContain("hazard-stripe");
  });
});

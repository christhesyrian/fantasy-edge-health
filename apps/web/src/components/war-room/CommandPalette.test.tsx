import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Recommendation } from "@/lib/types";

import { CommandPalette, type Command } from "./CommandPalette";

function player(uuid: string, name: string, position = "RB"): Recommendation {
  return {
    player_uuid: uuid,
    name,
    position,
    team: "SEA",
    overall_score: 50,
    model_rank: 1,
    recommendation: "STRONG_VALUE",
    components: [],
    reasons: [],
  } as Recommendation;
}

function setup(overrides: Partial<Parameters<typeof CommandPalette>[0]> = {}) {
  const commands: Command[] = [
    { id: "qb", label: "Show quarterbacks", keywords: "filter position", run: vi.fn() },
    { id: "theme", label: "Cycle theme", keywords: "dark light", run: vi.fn() },
  ];
  const props = {
    open: true,
    onClose: vi.fn(),
    commands,
    players: [player("p1", "Alpha Back"), player("p2", "Bravo Receiver", "WR")],
    onSelectPlayer: vi.fn(),
    ...overrides,
  };
  render(<CommandPalette {...props} />);
  return props;
}

describe("CommandPalette", () => {
  it("renders nothing when closed", () => {
    setup({ open: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists commands before anything is typed", () => {
    setup();
    expect(screen.getByText("Show quarterbacks")).toBeInTheDocument();
    // Four hundred names by default would bury the commands.
    expect(screen.queryByText("Alpha Back")).not.toBeInTheDocument();
  });

  it("finds players once a query is long enough", async () => {
    setup();
    await userEvent.type(screen.getByRole("textbox"), "alpha");
    expect(screen.getByText("Alpha Back")).toBeInTheDocument();
  });

  it("matches commands on their keywords", async () => {
    setup();
    await userEvent.type(screen.getByRole("textbox"), "dark");
    expect(screen.getByText("Cycle theme")).toBeInTheDocument();
  });

  it("runs a command and closes", async () => {
    const props = setup();
    await userEvent.click(screen.getByText("Show quarterbacks"));

    expect(props.commands[0].run).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("selects a player and closes", async () => {
    const props = setup();
    await userEvent.type(screen.getByRole("textbox"), "bravo");
    await userEvent.click(screen.getByText("Bravo Receiver"));

    expect(props.onSelectPlayer).toHaveBeenCalledWith("p2");
    expect(props.onClose).toHaveBeenCalled();
  });

  it("runs the highlighted entry on Enter", async () => {
    const props = setup();
    await userEvent.keyboard("{Enter}");
    expect(props.commands[0].run).toHaveBeenCalled();
  });

  it("moves the highlight with the arrow keys", async () => {
    const props = setup();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(props.commands[1].run).toHaveBeenCalled();
  });

  it("closes on Escape", async () => {
    const props = setup();
    await userEvent.keyboard("{Escape}");
    expect(props.onClose).toHaveBeenCalled();
  });

  it("says so plainly when nothing matches", async () => {
    setup();
    await userEvent.type(screen.getByRole("textbox"), "zzzzz");
    expect(screen.getByText(/nothing matches/i)).toBeInTheDocument();
  });
});

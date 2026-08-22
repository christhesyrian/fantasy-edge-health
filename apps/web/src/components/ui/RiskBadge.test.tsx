import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it("announces the risk in words for a screen reader", () => {
    // Colour and glyph are visual channels. A third, textual channel is what
    // makes the same information available to assistive technology.
    render(<RiskBadge score={78} band="SEVERE" />);
    expect(
      screen.getByText(/availability risk 78 out of 100, severe/i),
    ).toBeInTheDocument();
  });

  it("shows a distinct state for an unmeasured player", () => {
    // "Unknown" must never render as "low risk"; an unmeasured player is not
    // a safe one.
    render(<RiskBadge score={null} band={null} />);
    expect(screen.getByText(/availability risk unknown/i)).toBeInTheDocument();
  });

  it("renders the numeric score alongside the glyph", () => {
    render(<RiskBadge score={42.4} band="MODERATE" />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("can hide the number when a column is tight", () => {
    render(<RiskBadge score={42} band="MODERATE" showScore={false} />);
    expect(screen.queryByText("42")).not.toBeInTheDocument();
    expect(screen.getByText(/availability risk 42 out of 100/i)).toBeInTheDocument();
  });
});

import { describe, expect, it } from "vitest";

import { humanise, int, num, pct, riskGlyph, signed, since } from "./format";

describe("number formatting", () => {
  it("renders an em dash for a genuinely absent value", () => {
    // A missing value and a zero mean different things; showing "0" for
    // "unknown" would be a lie the whole product is built to avoid.
    expect(num(null)).toBe("—");
    expect(num(undefined)).toBe("—");
    expect(num(0)).toBe("0.0");
  });

  it("formats to the requested precision", () => {
    expect(num(53.789, 1)).toBe("53.8");
    expect(int(53.6)).toBe("54");
  });

  it("always signs a delta so a gain reads as one", () => {
    expect(signed(14)).toBe("+14.0");
    expect(signed(-3.2)).toBe("-3.2");
    expect(signed(0)).toBe("0.0");
    expect(signed(null)).toBe("—");
  });

  it("renders fractions as percentages", () => {
    expect(pct(0.42)).toBe("42%");
    expect(pct(1)).toBe("100%");
    expect(pct(null)).toBe("—");
  });
});

describe("riskGlyph", () => {
  it("encodes severity in shape, not only colour", () => {
    // The glyph is what makes risk legible in greyscale and to a colour-blind
    // reader; each band must be visually distinct from the others.
    const glyphs = ["LOW", "MODERATE", "ELEVATED", "SEVERE"].map(riskGlyph);
    expect(new Set(glyphs).size).toBe(4);
  });

  it("grows monotonically with severity", () => {
    const filled = (band: string) =>
      [...riskGlyph(band)].filter((character) => character === "▇").length;
    expect(filled("LOW")).toBeLessThan(filled("MODERATE"));
    expect(filled("MODERATE")).toBeLessThan(filled("ELEVATED"));
    expect(filled("ELEVATED")).toBeLessThan(filled("SEVERE"));
  });
});

describe("humanise", () => {
  it("turns an enum token into a label", () => {
    expect(humanise("DRAFT_NOW")).toBe("Draft Now");
    expect(humanise("HIP_GROIN")).toBe("Hip Groin");
  });
});

describe("since", () => {
  it("describes recency compactly", () => {
    expect(since(new Date(Date.now() - 5_000).toISOString())).toMatch(/^\d+s ago$/);
    expect(since(new Date(Date.now() - 300_000).toISOString())).toMatch(/^\d+m ago$/);
    expect(since(null)).toBe("unknown");
  });
});

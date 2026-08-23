import { expect, test, type Page } from "@playwright/test";

/**
 * The critical demo path, end to end.
 *
 * The acceptance criteria walked as a user would walk them: open demo mode, run
 * a draft, watch the board react, make a pick, inspect a player, compare
 * players. Assertions are about what is visible on screen rather than internal
 * state, so this fails when the *product* breaks rather than when a class is
 * renamed.
 */

async function startDemoDraft(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Fantasy Health Edge" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Enter the war room" }).click();
  await expect(page).toHaveURL(/\/war-room\//);
  await expect(page.getByTestId("panel-best-available")).toBeVisible();
  // Wait for the board itself, not just its container.
  await expect(page.locator("tbody tr").first()).toBeVisible();
}

/** Player names currently on the board, in rank order. */
async function boardNames(page: Page): Promise<string[]> {
  // The first span only: the cell also carries the team, so its innerText
  // would read "Beau DevereauxDEN".
  return page.locator("tbody tr td:nth-child(2) span:first-child").allInnerTexts();
}

test.describe("demo draft", () => {
  test("the landing page offers demo mode without any credentials", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Demo mode" })).toBeVisible();
    await expect(page.getByText("synthetic data").first()).toBeVisible();
    await expect(page.getByText(/no credentials, no ingestion/i)).toBeVisible();
  });

  test("a draft runs, the board reacts, and a pick lands", async ({ page }) => {
    await startDemoDraft(page);

    // Demo data is labelled as synthetic wherever it appears.
    await expect(page.getByText("Demo · synthetic data")).toBeVisible();

    const before = await boardNames(page);
    expect(before.length).toBeGreaterThan(10);

    // Advance to the user's pick. Picks arrive and the board changes.
    await page.getByRole("button", { name: /To my pick/i }).click();
    await expect(page.getByText("You are on the clock")).toBeVisible();

    const after = await boardNames(page);
    expect(after).not.toEqual(before);

    // The recommendation explains itself, on screen, without a disclosure.
    await expect(page.getByText("Why this score")).toBeVisible();
    await expect(page.getByText(/above the .*baseline/i).first()).toBeVisible();

    // Draft the recommended player.
    const heroName = await page.getByTestId("hero-player").innerText();
    await page.getByRole("button", { name: "Draft now", exact: true }).click();

    // The roster fills with that player.
    const roster = page.getByTestId("panel-my-roster");
    await expect(roster.getByText(heroName, { exact: false })).toBeVisible();

    // The ticker marks it as the user's own pick.
    await expect(
      page.getByTestId("panel-draft-ticker").getByText("YOU").first(),
    ).toBeVisible();

    // And it is no longer the user's turn.
    await expect(page.getByText("Not your pick")).toBeVisible();
  });

  test("drafted players leave the board", async ({ page }) => {
    await startDemoDraft(page);

    const before = await boardNames(page);
    await page.getByRole("button", { name: /To my pick/i }).click();
    await expect(page.getByText("You are on the clock")).toBeVisible();

    const ticker = await page
      .getByTestId("panel-draft-ticker")
      .locator("li")
      .allInnerTexts();
    expect(ticker.length).toBeGreaterThan(0);

    const after = await boardNames(page);
    // Every player in the ticker has been drafted and must be gone.
    for (const entry of ticker) {
      const drafted = before.find((name) => entry.includes(name.split(" ")[0]));
      if (drafted) expect(after).not.toContain(drafted);
    }
  });

  test("the player drawer shows health, timeline, and its own limitations", async ({
    page,
  }) => {
    await startDemoDraft(page);

    await page.locator("tbody tr").first().dblclick();

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    // exact:true, because "availability risk" also appears in the screen-reader
    // label and in the limitations paragraph — three matches, one intended.
    await expect(drawer.getByText("Availability risk", { exact: true })).toBeVisible();
    await expect(drawer.getByText("Injury timeline", { exact: true })).toBeVisible();
    await expect(drawer.getByText("Usage", { exact: true })).toBeVisible();

    // The product's central honesty claim, rendered rather than buried.
    await expect(drawer.getByText(/not medical outcomes/i)).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
  });

  test("two players can be compared side by side", async ({ page }) => {
    await startDemoDraft(page);

    const rows = page.locator("tbody tr");
    await rows
      .nth(0)
      .getByRole("button", { name: /^Compare / })
      .click();
    await rows
      .nth(1)
      .getByRole("button", { name: /^Compare / })
      .click();

    await expect(page.getByText("Compare · 2")).toBeVisible();
    await expect(page.getByText("Availability").first()).toBeVisible();
  });

  test("keyboard shortcuts advance the draft", async ({ page }) => {
    await startDemoDraft(page);

    const pick = page.getByTestId("current-pick");
    const before = await pick.innerText();
    await page.keyboard.press("n");
    await expect(pick).not.toHaveText(before);
  });

  test("the live feed reports its own connection state", async ({ page }) => {
    await startDemoDraft(page);
    // Server-sent events connect on load; the header says so out loud.
    await expect(page.getByTestId("connection-state")).toHaveText("LIVE");
  });

  test("the command palette finds a player by name", async ({ page }) => {
    await startDemoDraft(page);

    const names = await boardNames(page);
    const target = names[3];

    await page.keyboard.press("ControlOrMeta+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();

    await page.keyboard.type(target.slice(0, 5));
    await palette.getByText(target, { exact: true }).click();

    // The palette selects the player on the board and reveals the row, rather
    // than opening the drawer over it.
    const selected = page.locator('tbody tr[data-selected="true"]');
    await expect(selected).toHaveCount(1);
    await expect(selected.locator("td:nth-child(2) span:first-child")).toHaveText(
      target,
    );
    await expect(selected).toBeInViewport();
  });

  test("the command palette runs a filter command", async ({ page }) => {
    await startDemoDraft(page);

    await page.keyboard.press("ControlOrMeta+k");
    await page.getByRole("button", { name: "Show wide receivers" }).click();

    await expect(page.getByRole("dialog", { name: "Command palette" })).toBeHidden();
    // Every remaining row is a receiver. Receivers rather than quarterbacks:
    // in a one-QB league no quarterback is inside the top of the board at pick
    // one, so a QB filter is legitimately empty there.
    const positions = await page.locator("tbody tr td:nth-child(3)").allInnerTexts();
    expect(positions.length).toBeGreaterThan(0);
    expect(new Set(positions.map((text) => text.trim()))).toEqual(new Set(["WR"]));
  });

  test("a favourited player survives a reload", async ({ page }) => {
    await startDemoDraft(page);

    const first = page.locator("tbody tr").first();
    const name = (
      await first.locator("td:nth-child(2) span:first-child").innerText()
    ).trim();
    await first.getByRole("button", { name: `Star ${name}` }).click();
    await expect(first.getByRole("button", { name: `Unstar ${name}` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await page.reload();
    await expect(page.locator("tbody tr").first()).toBeVisible();

    // Favourites live in localStorage, so the star is still lit after a reload.
    await expect(page.getByRole("button", { name: `Unstar ${name}` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("the theme can be changed and is remembered", async ({ page }) => {
    await startDemoDraft(page);

    const toggle = page.getByRole("button", { name: /^theme:/ });
    await expect(toggle).toHaveText("theme: dark");

    await toggle.click();
    await expect(toggle).toHaveText("theme: light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await page.reload();
    await expect(page.getByRole("button", { name: /^theme:/ })).toHaveText(
      "theme: light",
    );
  });

  test("an unknown draft fails with a useful message, not a blank page", async ({
    page,
  }) => {
    await page.goto("/war-room/does-not-exist");

    await expect(page.getByText("Could not load this draft")).toBeVisible();
    await expect(page.getByRole("link", { name: "Start over" })).toBeVisible();
  });
});

import { expect, test, type Page } from "@playwright/test";

/**
 * Offline preview mode, end to end, with no API running at all.
 *
 * These are the assertions that keep preview honest. It must be genuinely
 * self-sufficient, it must never present recorded data as live, and it must
 * refuse what it cannot replay instead of inventing an outcome.
 */

async function enterWarRoom(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Enter the war room" }).click();
  await expect(page).toHaveURL(/\/war-room\//);
  await expect(page.locator("tbody tr").first()).toBeVisible();
}

test.describe("preview mode", () => {
  test("the war room renders with no backend", async ({ page }) => {
    await enterWarRoom(page);

    const rows = await page.locator("tbody tr").count();
    expect(rows).toBeGreaterThan(20);
    // Real engine output, not placeholders: scores and a breakdown.
    await expect(page.getByText("Why this score")).toBeVisible();
    await expect(page.getByText(/above the .*baseline/i).first()).toBeVisible();
  });

  test("it is labelled synthetic and never claims a live feed", async ({ page }) => {
    await enterWarRoom(page);

    await expect(page.getByTestId("preview-badge")).toHaveText(
      "PREVIEW · SYNTHETIC FIXTURES",
    );
    // The single most important assertion in this file.
    await expect(page.getByTestId("connection-state")).toHaveText("PREVIEW");
    await expect(page.getByTestId("connection-state")).not.toHaveText("LIVE");
  });

  test("the landing page reports preview as a degradation", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/running in degraded configuration/i)).toBeVisible();
  });

  test("advancing steps the draft one pick at a time", async ({ page }) => {
    await enterWarRoom(page);

    const pick = page.getByTestId("current-pick");
    await expect(pick).toHaveText("1 · R1");
    await page.keyboard.press("n");
    await expect(pick).toHaveText("2 · R1");
    await page.keyboard.press("n");
    await expect(pick).toHaveText("3 · R1");
  });

  test("drafted players leave the board", async ({ page }) => {
    await enterWarRoom(page);
    const before = await page
      .locator("tbody tr td:nth-child(2) span:first-child")
      .allInnerTexts();

    await page.keyboard.press("a");
    await expect(page.getByTestId("current-pick")).not.toHaveText("1 · R1");

    const ticker = await page
      .getByTestId("panel-draft-ticker")
      .locator("li")
      .allInnerTexts();
    expect(ticker.length).toBeGreaterThan(0);

    const after = await page
      .locator("tbody tr td:nth-child(2) span:first-child")
      .allInnerTexts();
    for (const entry of ticker) {
      const drafted = before.find((name) => entry.includes(name));
      if (drafted) expect(after).not.toContain(drafted);
    }
  });

  test("drafting is refused with a reason rather than faked", async ({ page }) => {
    await enterWarRoom(page);
    await page.keyboard.press("a");
    await expect(page.getByText("You are on the clock")).toBeVisible();

    await page.getByRole("button", { name: "Draft now", exact: true }).click();

    await expect(page.getByRole("status")).toContainText(
      "cannot draft a player of your choosing",
    );
  });

  test("connecting a Sleeper league is disabled, not broken", async ({ page }) => {
    await page.goto("/");

    const card = page.getByTestId("sleeper-preview-disabled");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Unavailable in preview");
    // No form to submit means no request that can fail confusingly.
    await expect(card.getByRole("textbox")).toHaveCount(0);
  });

  test("the player drawer opens with health and limitations", async ({ page }) => {
    await enterWarRoom(page);
    await page.locator("tbody tr").first().dblclick();

    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("Availability risk", { exact: true })).toBeVisible();
    await expect(drawer.getByText(/not medical outcomes/i)).toBeVisible();
  });

  test("the command palette works", async ({ page }) => {
    await enterWarRoom(page);

    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
    await page.getByRole("button", { name: "Show wide receivers" }).click();

    const positions = await page.locator("tbody tr td:nth-child(3)").allInnerTexts();
    expect(positions.length).toBeGreaterThan(0);
    expect(new Set(positions.map((text) => text.trim()))).toEqual(new Set(["WR"]));
  });

  test("favourites and theme persist across a reload", async ({ page }) => {
    await enterWarRoom(page);

    const first = page.locator("tbody tr").first();
    const name = (
      await first.locator("td:nth-child(2) span:first-child").innerText()
    ).trim();
    await first.getByRole("button", { name: `Star ${name}` }).click();
    await page.getByRole("button", { name: /^theme:/ }).click();

    await page.reload();
    await expect(page.locator("tbody tr").first()).toBeVisible();

    await expect(page.getByRole("button", { name: `Unstar ${name}` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("button", { name: /^theme:/ })).toHaveText(
      "theme: light",
    );
  });

  test("the end of the recording is stated, not hidden", async ({ page }) => {
    await enterWarRoom(page);

    // Walk past the last recorded snapshot.
    for (let step = 0; step < 14; step += 1) {
      await page.keyboard.press("n");
      await page.waitForTimeout(120);
    }

    await expect(page.getByRole("status")).toContainText(
      /end of the recorded preview/i,
    );
  });
});

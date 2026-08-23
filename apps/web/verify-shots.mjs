import { chromium } from "@playwright/test";

const BASE = "http://127.0.0.1:3210";
const OUT = "/tmp/agent-browser";

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 870, height: 643 },
  colorScheme: "dark",
});

await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Enter the war room" }).click();
await page.waitForURL(/\/war-room\//, { timeout: 30000 });
await page.getByText("Why this score").waitFor({ timeout: 30000 });
await page.waitForTimeout(1200);
await page.screenshot({ path: `${OUT}/verify-default.png` });

const rows = page.locator("tbody tr");
const count = await rows.count();
console.log("[v0] row count:", count);

await rows.nth(5).click();
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/verify-selected.png` });

await page.screenshot({
  path: `${OUT}/verify-panel.png`,
  clip: { x: 0, y: 0, width: 360, height: 643 },
});

await browser.close();
console.log("[v0] done");

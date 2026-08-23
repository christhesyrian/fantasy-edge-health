/**
 * Offline preview mode.
 *
 * A design tool or cloud preview can host this Next.js app but cannot host
 * Python, Postgres, and a live provider poller. Preview mode exists so that
 * environment still gets a working war room to design against.
 *
 * What it is NOT: a mock layer that stands in for the API on the production
 * path. It is off unless explicitly switched on at build time, it replays
 * responses recorded from the real engine rather than inventing any, it is
 * labelled synthetic wherever it appears, it never reports a live connection,
 * and it refuses every action that would need a real backend.
 *
 * The flag is an inlined build-time constant, not a runtime setting: a build
 * made without it resolves `api` to the live client, and no user action can
 * reach the preview adapter.
 *
 * The recording itself is emitted as a lazily-imported chunk, so it exists in a
 * normal build's output but is never referenced by the app manifest and never
 * fetched by a browser. Measured at 686 KiB, downloaded only when preview code
 * actually runs.
 */
export const PREVIEW_MODE = process.env.NEXT_PUBLIC_PREVIEW_MODE === "fixtures";

/** Shown wherever preview data is on screen. Never soften this wording. */
export const PREVIEW_BANNER = "PREVIEW · SYNTHETIC FIXTURES";

/**
 * Why an action is refused in preview. Phrased as a fact about the environment
 * rather than an error, because nothing has gone wrong.
 */
export const PREVIEW_UNAVAILABLE =
  "Not available in preview mode — this needs the Fantasy Health Edge API. " +
  "Run it locally with `make dev-api`, or point NEXT_PUBLIC_API_BASE_URL at a " +
  "deployed backend.";

/** Why the draft buttons do nothing in preview. */
export const PREVIEW_DRAFT_REFUSED =
  "Preview replays a recorded draft, so it cannot draft a player of your " +
  "choosing. Advancing, inspecting, comparing, and favouriting all work.";

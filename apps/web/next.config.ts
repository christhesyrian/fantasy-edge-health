import type { NextConfig } from "next";

/**
 * Standalone output is for the container image only.
 *
 * The Dockerfile copies `.next/standalone` so the runtime layer ships the
 * server plus its traced dependencies rather than the whole `node_modules`
 * tree. A managed platform builds its own output format instead and does not
 * want this, so it is opt-in via the Dockerfile rather than always on — a
 * build target should be asked for, not inferred.
 */
const standalone = process.env.NEXT_OUTPUT_STANDALONE === "1";

const nextConfig: NextConfig = {
  ...(standalone ? { output: "standalone" as const } : {}),
  ...(process.env.V0_VERIFY_DIST_DIR
    ? { distDir: process.env.V0_VERIFY_DIST_DIR }
    : {}),

  // The dev server refuses asset requests from a host it does not expect, which
  // breaks anything driving it over 127.0.0.1 rather than localhost — including
  // the end-to-end suite. Both spellings are the same machine.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;

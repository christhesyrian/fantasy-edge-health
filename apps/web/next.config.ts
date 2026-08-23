import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output keeps the container image to the server plus its traced
  // dependencies rather than the whole node_modules tree.
  output: "standalone",

  // The dev server refuses asset requests from a host it does not expect, which
  // breaks anything driving it over 127.0.0.1 rather than localhost — including
  // the end-to-end suite. Both spellings are the same machine.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;

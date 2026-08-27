import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Enables apps/web/Dockerfile standalone runtime for container hosts.
  // Vercel deploys ignore this and use the platform build pipeline.
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname),
  reactStrictMode: true,
};

export default nextConfig;

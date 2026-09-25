import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The engine is reached through this app's own /api/engine proxy, never
  // directly from the browser — so the operator API key stays server-side and
  // there is no cross-origin surface to configure.
  env: {},
  experimental: { optimizePackageImports: ["lucide-react", "recharts"] },
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;

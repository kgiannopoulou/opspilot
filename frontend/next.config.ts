import type { NextConfig } from "next";

const apiUrl = process.env.OPSPILOT_API_URL ?? "http://localhost:8010";

const nextConfig: NextConfig = {
  output: "standalone",
  // Same-origin proxy: the browser only ever talks to the Next.js origin, so the
  // httpOnly refresh-token cookie stays first-party and no CORS preflights are needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;

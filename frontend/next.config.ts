import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  trailingSlash: false,
  // @ts-ignore - proxyTimeout is a custom extension for turbopack/webpack proxy
  proxyTimeout: 60_000, // 60s for slow LLM endpoints
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8001";
    return [
      // Map collection endpoints that FastAPI expects to have trailing slashes
      {
        source: '/api/workflows',
        destination: `${backendUrl}/api/workflows/`,
      },
      {
        source: '/api/client_pools',
        destination: `${backendUrl}/api/client_pools/`,
      },
      {
        source: '/api/personas',
        destination: `${backendUrl}/api/personas/`,
      },
      {
        source: '/api/email_accounts',
        destination: `${backendUrl}/api/email_accounts/`,
      },
      {
        source: '/api/replies',
        destination: `${backendUrl}/api/replies/`,
      },
      // Fallback for all other endpoints
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

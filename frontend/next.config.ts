import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  deploymentId: process.env.DEPLOYMENT_VERSION,
  trailingSlash: false,
  allowedDevOrigins: ['127.0.0.1'],
  async redirects() {
    return [
      { source: '/dashboard/work', destination: '/dashboard', permanent: false },
      { source: '/dashboard/find-customers', destination: '/dashboard/customers?view=find', permanent: false },
      // /dashboard/get-started is the 5-step activation wizard; keep it independent
      { source: '/dashboard/campaigns', destination: '/dashboard/admin/plans', permanent: false },
      { source: '/dashboard/opportunities', destination: '/dashboard/results?view=opportunities', permanent: false },
      { source: '/dashboard/analytics', destination: '/dashboard/results?view=analytics', permanent: false },
    ];
  },
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
      {
        source: '/api/notifications',
        destination: `${backendUrl}/api/notifications/`,
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

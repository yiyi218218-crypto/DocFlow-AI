import type { NextConfig } from 'next';

const API_URL = process.env.API_INTERNAL_URL ?? 'http://localhost:8000';

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // The repo root holds a lockfile for the mermaid gate; pin tracing to this app.
  outputFileTracingRoot: __dirname,

  // Proxy the API through the same origin. That keeps the refresh cookie
  // first-party (no SameSite/CORS credential dance in development) and means the
  // browser never needs to know the API's address.
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: `${API_URL}/api/v1/:path*` }];
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Permissions-Policy', value: 'geolocation=(), microphone=(), camera=()' },
        ],
      },
    ];
  },

  // pdfjs ships its worker as a separate asset; Next does not bundle it
  // correctly by default, so it is imported with `?url` in the viewer.
  webpack: (config) => {
    config.resolve.alias = { ...config.resolve.alias, canvas: false };
    return config;
  },
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8787/api/:path*' },
    ]
  },
  reactStrictMode: true,
}
module.exports = nextConfig

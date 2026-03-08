/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === "production";
const assumeTls = process.env.ICEA_ASSUME_TLS === "true";

// NOTE (Hospital Pilot):
// - Next.js hydration commonly requires inline scripts unless nonces/hashes are implemented end-to-end.
// - Recharts/Tailwind commonly rely on inline styles.
// - We keep everything else tight and allow only the minimum relaxations needed for a functional NCC UI.
// - Full CSP nonces (removing 'unsafe-inline' for scripts) requires a custom Next.js middleware to generate
//   per-request nonces and inject them into all inline scripts. This is deferred to the next major release
//   to avoid destabilizing the current pilot UI.
const scriptSrc = isProd
  ? "script-src 'self' 'unsafe-inline';"
  : "script-src 'self' 'unsafe-inline' 'unsafe-eval';";
const styleSrc = "style-src 'self' 'unsafe-inline';";

const csp = `
  default-src 'self';
  base-uri 'none';
  object-src 'none';
  frame-ancestors 'none';
  frame-src 'none';
  form-action 'self';
  img-src 'self' data: blob:;
  font-src 'self' data:;
  ${styleSrc}
  style-src-attr 'unsafe-inline';
  ${scriptSrc}
  script-src-attr 'none';
  connect-src 'self';
  manifest-src 'self';
  worker-src 'self' blob:;
  ${assumeTls ? "upgrade-insecure-requests; block-all-mixed-content;" : ""}
`.replace(/\s{2,}/g, " ").trim();

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "X-DNS-Prefetch-Control", value: "off" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-site" },
  // HSTS only when the deployment is known to be served over HTTPS (terminated by hospital reverse proxy).
  ...(assumeTls ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" }] : []),
];

/**
 * ICEA+ Nursing Command Center (NCC)
 * - BFF-first: never expose backend tokens to the browser
 * - Standalone output for hardened container deployment
 */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  env: {},
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;

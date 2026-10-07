import type { NextConfig } from "next"

const demo = process.env.NEXT_PUBLIC_DEMO === "1"

const nextConfig: NextConfig = demo
  ? {
      // The public read-only demo (make demo-site): a static site, e.g. for GitHub Pages.
      output: "export",
      basePath: process.env.NEXT_PUBLIC_BASE_PATH || undefined,
      trailingSlash: true,
      images: { unoptimized: true },
      poweredByHeader: false,
    }
  : {
      // A self-contained server for the Docker image (infra/helm/relay).
      output: "standalone",
      poweredByHeader: false,
      // No dev-mode badge over the UI (it would show in recorded demos).
      devIndicators: false,
    }

export default nextConfig

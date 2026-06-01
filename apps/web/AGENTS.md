<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

<!-- END:nextjs-agent-rules -->

## Dev server uses webpack, not Turbopack

`npm run dev` runs `next dev --webpack` on purpose. Under Turbopack (the Next 16
default), Tailwind v4's custom `@theme` tokens (e.g. the `brand-*` crimson scale
in `app/globals.css`) are dropped on incremental recompile — the page keeps
rendering but brand backgrounds/text disappear (white-on-white) until a clean
restart. Webpack dev compiles the theme deterministically and survives HMR.
`next build` (production) is unaffected. Revisit if a future Next/Turbopack
release fixes the `@theme` recompile bug.

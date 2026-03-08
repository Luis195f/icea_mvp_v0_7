# ICEA+ Nursing Command Center — production container (Next.js Standalone)
# - Multi-stage build
# - Standalone output: minimal runtime footprint
# - Non-root runtime user (uid/gid 10001, ENS-friendly)
# - Deterministic dependency install via npm ci
#
# IMPORTANT (Determinism): package-lock.json MUST be committed to the repo.
# If you change dependencies locally, run: npm install && commit the updated package-lock.json.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

# Create non-root user with stable uid/gid
RUN addgroup -g 10001 -S nextjs && adduser -u 10001 -S nextjs -G nextjs

# Copy standalone server + minimal assets
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER 10001:10001
EXPOSE 3000

CMD ["node", "server.js"]

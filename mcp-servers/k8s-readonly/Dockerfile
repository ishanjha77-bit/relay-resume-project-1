# syntax=docker/dockerfile:1
# Runs the TypeScript sources directly with Node's type stripping: no build step.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --no-audit --no-fund

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S app && adduser -S -G app -u 10001 app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src
USER 10001
EXPOSE 8000
CMD ["node", "--experimental-strip-types", "--disable-warning=ExperimentalWarning", "src/main.ts"]

# Build context: frontend/
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules node_modules
COPY . .
# Rewrites are resolved at build time, so the API address of the compose network is baked in here.
ARG OPSPILOT_API_URL=http://api:8000
ENV OPSPILOT_API_URL=$OPSPILOT_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# The server only needs `node`. The package managers bundled with the base image are never used at runtime and
# carry their own dependency CVEs (Trivy: brace-expansion, picomatch, pacote, sigstore …), so they are removed.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn* \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static .next/static
COPY --from=build --chown=node:node /app/edge.js ./edge.js
USER node
EXPOSE 3000
# edge.js sets X-Forwarded-For from the TCP peer, then starts the standalone server (server.js).
CMD ["node", "edge.js"]

# syntax=docker/dockerfile:1
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.30.4-alpine AS runtime
# CVE-2026-14456 (openssl DoS via unbounded QUIC memory growth): this base
# image ships openssl 3.5.7-r0; the alpine 3.24 repo carries the fixed
# 3.5.8-r0 (landed 2026-08-25, after every published nginx:*-alpine build).
# Upgrade the two runtime openssl libraries in place at build time.
RUN apk upgrade --no-cache libssl3 libcrypto3
ARG VERSION=1.0.1-rc1
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Cyber Agent Platform Console" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html
# non-root master: nginx writes its pid file to /run by default, which uid 101
# cannot open; redirect the pid to /tmp (world-writable) so the image runs
# cleanly under runAsNonRoot (Kubernetes).
RUN sed -i 's|/run/nginx.pid|/tmp/nginx.pid|g' /etc/nginx/nginx.conf \
    && chown -R 101:101 /var/cache/nginx /var/run /var/log/nginx /etc/nginx/conf.d /etc/nginx/templates /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/

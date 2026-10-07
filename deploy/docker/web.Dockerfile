# syntax=docker/dockerfile:1.7
# Web UI: built with Node, served by unprivileged nginx which also proxies /api to the api service.

FROM node:22-alpine AS build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY apps/web ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.30-alpine AS web
# Pick up Alpine security fixes published after the base image was built.
USER root
RUN apk upgrade --no-cache
USER 101
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/docker/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1

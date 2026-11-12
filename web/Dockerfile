# The web app of Sudoo.sh: built with Node, served by Caddy (a single small binary).
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM caddy:2-alpine
LABEL org.opencontainers.image.title="Sudoo.sh web" \
      org.opencontainers.image.source="https://github.com/Yosef-AlSabbah/sudoo.sh" \
      org.opencontainers.image.authors="Yousef M. Y. Al Sabbah"
COPY Caddyfile /etc/caddy/Caddyfile
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --from=build /app/dist /srv
ENTRYPOINT ["/entrypoint.sh"]

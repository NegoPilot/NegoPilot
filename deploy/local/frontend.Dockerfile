FROM node:22-alpine AS build
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src/ ./src/
COPY frontend/scripts/ ./scripts/
RUN npm run check:api && npm run build

FROM nginx:stable-alpine
COPY deploy/local/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

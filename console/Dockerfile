# Operator console (React/Vite). Multi-stage: build the static SPA, then serve it.
# Separate from edge/ and center/ — it only talks to the center's API over HTTP.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
# VITE_API_BASE bakes the center's API origin into the build; override at build time.
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA fallback + proxy /api to the center config-service.
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

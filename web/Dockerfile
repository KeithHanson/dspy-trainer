FROM node:22-alpine AS build

ARG VITE_API_BASE_URL=/api
ARG VITE_MLFLOW_BASE_URL=/mlflow

ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_MLFLOW_BASE_URL=${VITE_MLFLOW_BASE_URL}

WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html ./index.html
COPY web/vite.config.js ./vite.config.js
COPY web/vitest.setup.js ./vitest.setup.js
COPY web/src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

FROM node:20-alpine AS build

WORKDIR /app
COPY web/package.json ./package.json
RUN npm install
COPY web/index.html ./index.html
COPY web/vite.config.js ./vite.config.js
COPY web/vitest.setup.js ./vitest.setup.js
COPY web/src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

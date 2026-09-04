# Dev image for the demo UI. Dependencies are installed at build time (fast,
# layer-cached) rather than through a slow bind-mount at container start.
FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM node:20-alpine AS deps

WORKDIR /app

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./

FROM deps AS dev
RUN chown -R node:node /app
USER node
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM deps AS build
RUN npm run build
CMD ["sh", "-c", "test -f dist/index.html"]

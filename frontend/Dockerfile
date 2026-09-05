FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

ARG VITE_API_BASE_URL=
ARG VITE_DEFAULT_SOURCE_LANGUAGE=auto
ARG VITE_DEFAULT_TARGET_LANGUAGE=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_DEFAULT_SOURCE_LANGUAGE=$VITE_DEFAULT_SOURCE_LANGUAGE \
    VITE_DEFAULT_TARGET_LANGUAGE=$VITE_DEFAULT_TARGET_LANGUAGE

RUN npm run build

FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache wget

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

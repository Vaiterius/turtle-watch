# Stage 1: build the React frontend. Node exists only here.
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the runtime image. Python, Flask, Waitress and the built assets.
FROM python:3.14-alpine
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
RUN apk add --no-cache tzdata
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/*.py ./
COPY --from=web /web/dist ./static
USER 1000:1000
EXPOSE 8000
CMD ["python", "main.py", "serve"]

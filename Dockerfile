FROM golang:1.23-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o solo-trace ./cmd/api/main.go

# Use Debian slim — Alpine's musl libc breaks PyTorch/demucs/transformers wheels
FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Go binary
WORKDIR /app
COPY --from=builder /app/solo-trace .

# Copy Python scripts and install deps
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -r scripts/requirements.txt

# Copy schema for migrations
COPY sql/ ./sql/

ENV ENVIRONMENT=production
ENV SERVER_PORT=4000
# HuggingFace model cache — points to the persistent /data volume so models
# are downloaded once and survive redeploys
ENV HF_HOME=/data/hf_cache
EXPOSE 4000

CMD ["./solo-trace"]

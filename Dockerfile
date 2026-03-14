FROM golang:1.23-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o solo-grabber ./cmd/api/main.go

FROM alpine:latest
RUN apk --no-cache add ca-certificates ffmpeg curl python3 py3-pip

# Copy Go binary
WORKDIR /app
COPY --from=builder /app/solo-grabber .

# Copy Python scripts and install deps
COPY scripts/ ./scripts/
RUN cd scripts && pip install --break-system-packages -r requirements.txt

# Copy schema for migrations
COPY sql/ ./sql/

COPY /scripts/me_fingerprint.json /data/me_fingerprint.json

ENV ENVIRONMENT=production
ENV SERVER_PORT=4000
EXPOSE 4000

CMD ["./solo-grabber"]

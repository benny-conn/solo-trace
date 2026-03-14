.PHONY: run build tidy

run:
	go run ./cmd/api/main.go

build:
	go build -o solo-grabber ./cmd/api/main.go

tidy:
	go mod tidy

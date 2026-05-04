# syntax=docker/dockerfile:1

FROM golang:1.25-alpine AS builder

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w" -o /out/fiveserver ./cmd/fiveserver

FROM alpine:3.21

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app

COPY --from=builder /out/fiveserver /app/fiveserver

EXPOSE 16001 18001 19501 20100 20101 20102 20103 20104

# Config must be mounted at runtime, e.g.:
#   -v ./etc/conf/fiveserver.yaml:/app/config/fiveserver.yaml:ro
ENTRYPOINT ["/app/fiveserver"]
CMD ["-config", "/app/config/fiveserver.yaml"]
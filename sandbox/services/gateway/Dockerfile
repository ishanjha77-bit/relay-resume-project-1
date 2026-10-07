# syntax=docker/dockerfile:1
# Build context: sandbox/services (the gateway depends on ../golib).
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY golib/go.mod golib/go.sum ./golib/
COPY gateway/go.mod gateway/go.sum ./gateway/
RUN --mount=type=cache,target=/go/pkg/mod cd gateway && go mod download
COPY golib ./golib
COPY gateway ./gateway
ARG VERSION=dev
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd gateway && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/gateway .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
EXPOSE 8080
ENTRYPOINT ["/gateway"]

# syntax=docker/dockerfile:1
# Build context: sandbox/services (payments depends on ../golib).
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY golib/go.mod golib/go.sum ./golib/
COPY payments/go.mod payments/go.sum ./payments/
RUN --mount=type=cache,target=/go/pkg/mod cd payments && go mod download
COPY golib ./golib
COPY payments ./payments
ARG VERSION=dev
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd payments && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/payments .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/payments /payments
EXPOSE 8080
ENTRYPOINT ["/payments"]

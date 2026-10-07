# Relay — everything runs on a local kind cluster.
#   make up                         sandbox + observability (roadmap weeks 1–2)
#   make chaos scenario=db-pool     break something on purpose
#   make reset                      undo every injected fault
# On Windows run make from Git Bash (or WSL): recipes are bash.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

CLUSTER ?= relay
CONTEXT := kind-$(CLUSTER)
KUBECTL := kubectl --context $(CONTEXT)
HELM    := helm --kube-context $(CONTEXT)

include sandbox/versions.env
export GATEWAY_VERSION ORDERS_VERSION PAYMENTS_VERSION INVENTORY_VERSION CLUSTER CONTEXT

# Docker on Windows needs a Windows path for bind mounts.
ifeq ($(OS),Windows_NT)
HOST_PWD = $$(cygpath -m "$$(pwd)")
else
HOST_PWD = $$(pwd)
endif

SANDBOX_IMAGES := relay/sandbox-gateway:$(GATEWAY_VERSION) \
                  relay/sandbox-orders:$(ORDERS_VERSION) \
                  relay/sandbox-payments:$(PAYMENTS_VERSION) \
                  relay/sandbox-inventory:$(INVENTORY_VERSION)

# Relay's own services. The Python images share one Dockerfile: PACKAGE picks
# the workspace member, COMMAND its entry point.
RELAY_SERVICES := platform-api agent-service mcp-logs mcp-metrics mcp-k8s mcp-runbooks mcp-github console
# Build contexts reach Docker as a tar stream: tar reads files the ordinary
# way, while Docker's own context walker fails on files that cloud-sync tools
# (OneDrive) have turned into placeholders.
TAR := tar -cf - --exclude=node_modules --exclude=__pycache__ --exclude=.pytest_cache --exclude=.venv \
       --exclude=target --exclude=.next --exclude=test-results --exclude=playwright-report
PY_CONTEXT := $(TAR) --exclude=tests --exclude=mcp-servers/k8s-readonly pyproject.toml uv.lock \
              infra/docker/python-service.Dockerfile apps/agent-service mcp-servers
PY_BUILD := docker build -f infra/docker/python-service.Dockerfile

##@ Lifecycle

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

.PHONY: up
up: cluster sandbox-images release-images load-sandbox observability sandbox relay-images relay ## Everything: cluster, observability, sandbox, Relay
	@echo
	@echo "Grafana     http://localhost:3001"
	@echo "Prometheus  http://localhost:9090   Alertmanager http://localhost:9093"
	@echo "Jaeger      http://localhost:16686  Shop API     http://localhost:8088/api/products"
	@echo "Relay API   http://localhost:8081   Agent        http://localhost:8000/runs"

.PHONY: down
down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

.PHONY: cluster
cluster: ## Create the kind cluster (idempotent)
	@kind get clusters 2>/dev/null | grep -qx '$(CLUSTER)' \
	  || kind create cluster --name $(CLUSTER) --config infra/kind-cluster.yaml --wait 120s

.PHONY: status
status: ## Show every pod in the cluster
	$(KUBECTL) get pods -A -o wide

##@ Images

.PHONY: sandbox-images
sandbox-images: ## Build the sandbox service images
	docker build -t relay/sandbox-gateway:$(GATEWAY_VERSION) --build-arg VERSION=$(GATEWAY_VERSION) \
	  -f sandbox/services/gateway/Dockerfile sandbox/services
	docker build -t relay/sandbox-payments:$(PAYMENTS_VERSION) --build-arg VERSION=$(PAYMENTS_VERSION) \
	  -f sandbox/services/payments/Dockerfile sandbox/services
	docker build -t relay/sandbox-orders:$(ORDERS_VERSION) sandbox/services/orders
	docker build -t relay/sandbox-inventory:$(INVENTORY_VERSION) sandbox/services/inventory

.PHONY: relay-images
relay-images: ## Build Relay's images (platform API, agent service, MCP servers) and load them into kind
	@for svc in $(RELAY_SERVICES); do "$(MAKE)" relay-image svc=$$svc; done
	kind load docker-image --name $(CLUSTER) $(foreach s,$(RELAY_SERVICES),relay/$(s):dev)

.PHONY: relay-image
relay-image:
	@case "$(svc)" in \
	  platform-api)  (cd apps/platform-api && $(TAR) .) | docker build -t relay/platform-api:dev - ;; \
	  console)       (cd apps/console && $(TAR) .) | docker build -t relay/console:dev - ;; \
	  mcp-k8s)       (cd mcp-servers/k8s-readonly && $(TAR) .) | docker build -t relay/mcp-k8s:dev - ;; \
	  agent-service) $(PY_CONTEXT) | $(PY_BUILD) -t relay/agent-service:dev --build-arg PACKAGE=relay-agent-service --build-arg COMMAND="relay-agent serve" - ;; \
	  mcp-logs)      $(PY_CONTEXT) | $(PY_BUILD) -t relay/mcp-logs:dev --build-arg PACKAGE=relay-mcp-logs-loki --build-arg COMMAND=relay-mcp-logs-loki - ;; \
	  mcp-metrics)   $(PY_CONTEXT) | $(PY_BUILD) -t relay/mcp-metrics:dev --build-arg PACKAGE=relay-mcp-metrics-prometheus --build-arg COMMAND=relay-mcp-metrics-prometheus - ;; \
	  mcp-github)    $(PY_CONTEXT) | $(PY_BUILD) -t relay/mcp-github:dev --build-arg PACKAGE=relay-mcp-github --build-arg COMMAND=relay-mcp-github - ;; \
	  mcp-runbooks)  $(PY_CONTEXT) | $(PY_BUILD) -t relay/mcp-runbooks:dev --build-arg PACKAGE=relay-mcp-runbooks --build-arg COMMAND=relay-mcp-runbooks \
	                   --build-arg PREPARE="python -m runbooks.embed /app/models && chown -R 10001 /app/models" - ;; \
	  *) echo "usage: make relay-image svc=<$(RELAY_SERVICES)>"; exit 1 ;; \
	esac

.PHONY: release-images
release-images: ## Build the faulty "release" images used by bad-deploy scenarios
	bash sandbox/releases/build.sh

.PHONY: load-sandbox
load-sandbox: ## Load sandbox images (baseline + releases) into kind
	kind load docker-image --name $(CLUSTER) $(SANDBOX_IMAGES) $$(bash sandbox/releases/build.sh --list)

.PHONY: redeploy
redeploy: ## Rebuild one sandbox service, load it and restart its pod: make redeploy svc=orders
	@case "$(svc)" in gateway|payments|orders|inventory) ;; *) echo "usage: make redeploy svc=<gateway|payments|orders|inventory>"; exit 1;; esac
	@ver="$$(grep -i '^$(svc)_VERSION=' sandbox/versions.env | cut -d= -f2)"; \
	case "$(svc)" in \
	  gateway|payments) docker build -t relay/sandbox-$(svc):$$ver --build-arg VERSION=$$ver -f sandbox/services/$(svc)/Dockerfile sandbox/services ;; \
	  *) docker build -t relay/sandbox-$(svc):$$ver sandbox/services/$(svc) ;; \
	esac; \
	images="relay/sandbox-$(svc):$$ver"; \
	if [ "$(svc)" = orders ]; then bash sandbox/releases/build.sh; images="$$images $$(bash sandbox/releases/build.sh --list)"; fi; \
	kind load docker-image --name $(CLUSTER) $$images
	@# Deleting the pod (not `rollout restart`) keeps dev rebuilds out of the deploy history the agent reads.
	$(KUBECTL) -n sandbox delete pod -l app.kubernetes.io/name=$(svc) --wait=false
	$(KUBECTL) -n sandbox rollout status deploy/$(svc) --timeout=300s

##@ Deploy

.PHONY: observability
observability: ## Install Prometheus, Alertmanager, Loki, Jaeger, Grafana and the OTel Collector
	$(HELM) upgrade --install observability infra/observability \
	  --namespace observability --create-namespace --wait --timeout 5m

.PHONY: sandbox
sandbox: ## Install the system under test (4 services, Postgres, Redis, load generator)
	@# --force-conflicts: Helm owns the baseline; chaos scripts patch fields temporarily
	@# (server-side apply would otherwise refuse to take them back).
	$(HELM) upgrade --install sandbox infra/helm/sandbox \
	  --namespace sandbox --create-namespace --wait --timeout 10m --force-conflicts \
	  --set gateway.version=$(GATEWAY_VERSION) \
	  --set orders.version=$(ORDERS_VERSION) \
	  --set payments.version=$(PAYMENTS_VERSION) \
	  --set inventory.version=$(INVENTORY_VERSION)

.PHONY: relay-secrets
relay-secrets: ## Create/update the relay Secret: API key from .env; generated keys and tokens are kept
	bash scripts/relay-secrets.sh

.PHONY: relay
relay: relay-secrets ## Install Relay: Postgres (pgvector), Redis, Gitea, platform API, agent service, MCP servers
	$(HELM) upgrade --install relay infra/helm/relay --namespace relay --create-namespace --wait --timeout 10m
	"$(MAKE)" deploy-repo

.PHONY: deploy-repo
deploy-repo: ## Set up the sandbox's deploy repo on the local Gitea (idempotent): bots, protection, seed
	uv run --no-project --quiet python scripts/deploy_repo.py bootstrap

.PHONY: gitea-ui
gitea-ui: ## Browse the deploy repo and Relay's draft PRs: http://localhost:3003 (Ctrl-C to stop)
	$(KUBECTL) -n relay port-forward svc/gitea 3003:3000

.PHONY: relay-restart
relay-restart: ## Rebuild one Relay service and restart it: make relay-restart svc=agent-service
	"$(MAKE)" relay-image svc=$(svc)
	kind load docker-image --name $(CLUSTER) relay/$(svc):dev
	$(KUBECTL) -n relay rollout restart deploy/$(svc)
	$(KUBECTL) -n relay rollout status deploy/$(svc) --timeout=300s

##@ Chaos

.PHONY: chaos
chaos: ## Inject a fault, e.g. make chaos scenario=db-pool (see sandbox/chaos/)
	@test -n "$(scenario)" || { echo "usage: make chaos scenario=<name>"; ls sandbox/chaos/*.sh | xargs -n1 basename | sed 's/\.sh$$//' | grep -vE '^(lib|reset)$$'; exit 1; }
	bash sandbox/chaos/$(scenario).sh

.PHONY: reset
reset: ## Undo every injected fault and wait for the sandbox to be healthy
	bash sandbox/chaos/reset.sh

##@ Agent

.PHONY: mcp-local
mcp-local: ## Run the MCP servers locally against the cluster (Ctrl-C to stop)
	bash scripts/mcp-local.sh

.PHONY: llm-check
llm-check: ## Check the configured model answers with the key in .env (one tiny request)
	uv run --package relay-agent-service relay-agent llm-check $(if $(model),--model $(model),)

.PHONY: investigate
investigate: ## Investigate the alerts firing now (needs a key in .env and `make mcp-local`)
	uv run --package relay-agent-service relay-agent investigate $(if $(record),--record $(record),)

.PHONY: replay
replay: ## Replay a recorded investigation offline: make replay run=evals/recordings/<name>
	uv run --package relay-agent-service relay-agent investigate --replay $(run)

.PHONY: eval
eval: ## Score Relay on the chaos scenarios, end to end: make eval [scenarios=a,b] [batch=name] [label="..."]
	uv run python evals/runner.py $(if $(scenarios),--scenarios $(scenarios),) $(if $(batch),--batch $(batch),) $(if $(label),--label "$(label)",)

.PHONY: demo-site
demo-site: ## Static read-only demo of the console from recorded incidents: make demo-site [batches=a,b] [extra=INC-24] [base=/relay]
	python scripts/demo_site.py $(if $(batches),--batches $(batches),) $(if $(extra),--extra $(extra),)
	cd apps/console && NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH=$(base) npx next build
	@echo "the demo is in apps/console/out: serve it with  npx serve apps/console/out"

.PHONY: eval-retrieval
eval-retrieval: ## Score runbook search (keywords vs vectors vs hybrid) on labelled queries; no LLM needed
	uv run python evals/retrieval.py

.PHONY: eval-report
eval-report: ## Rebuild a batch's scorecard from its results: make eval-report batch=2026-10-05
	uv run python evals/runner.py --report --batch $(batch)

.PHONY: smoke
smoke: ## End to end in the cluster: fault -> alert -> incident -> agent verdict: make smoke scenario=db-pool
	bash scripts/smoke.sh $(or $(scenario),db-pool)

##@ Test

.PHONY: test
test: test-python test-node test-go test-java ## Run all unit tests

.PHONY: test-node
test-node: ## TypeScript: the k8s MCP server's tests; the console's lint and type check
	cd mcp-servers/k8s-readonly && npm ci --no-audit --no-fund --silent && npm run lint && npm test
	cd apps/console && npm ci --no-audit --no-fund --silent && npm run lint && npx tsc --noEmit

.PHONY: test-e2e
test-e2e: ## Console end-to-end tests (Playwright, recorded API responses; no backend needed)
	cd apps/console && npx playwright install chromium && npx playwright test

.PHONY: test-python
test-python: ## Python tests + lint (agent service, MCP servers)
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest

.PHONY: test-go
test-go: ## Go tests (run in Docker; no local Go needed)
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(HOST_PWD)/sandbox/services:/src" -v relay-gomod:/go/pkg/mod \
	  -w /src golang:1.27-alpine \
	  sh -c 'for m in golib gateway payments; do (cd $$m && go vet ./... && go test ./...); done'

.PHONY: test-java
test-java: ## Java tests: sandbox services and the platform API (Testcontainers needs Docker)
	cd sandbox/services/orders && sh ./mvnw -B -q test
	cd sandbox/services/inventory && sh ./mvnw -B -q test
	cd apps/platform-api && sh ./mvnw -B -q test

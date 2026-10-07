"""The sandbox's deploy repository, on the Gitea that runs in the cluster.

GitOps in miniature: shop/deploy holds one file per sandbox service (its image
and environment), and every rollout is a commit whose message says why. The
agents read it through the github MCP server; Relay proposes fixes to it as
draft pull requests.

    python scripts/deploy_repo.py bootstrap                  once, after `make relay`
    python scripts/deploy_repo.py record <service> "<why>"   after a rollout (sandbox/chaos/lib.sh)

Two bots, least privilege: ci-bot (CD) may push to main; relay-bot (Relay)
may push branches and open pull requests, but main is protected from it and a
merge needs an approving review it can't give itself. Their tokens live in the
relay-secrets Secret. Standard library only; Gitea is reached through
`kubectl exec`, so it needs no published port.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time

CONTEXT = os.environ.get("CONTEXT", "kind-relay")
NAMESPACE = "relay"
SANDBOX = "sandbox"
SECRET = "relay-secrets"
REPO = "shop/deploy"
SERVICES = ("gateway", "orders", "payments", "inventory")
ADMIN = "relay-admin"
CI = ("ci-bot", "ci@shop.local")
SECRET_ENV = re.compile(r"PASSWORD|SECRET|TOKEN|KEY", re.IGNORECASE)


def kubectl(*args: str, stdin: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, *args], input=stdin, capture_output=True, check=check
    )


def secret_value(key: str) -> str:
    out = kubectl("-n", NAMESPACE, "get", "secret", SECRET, "-o", f"jsonpath={{.data.{key}}}", check=False)
    return base64.b64decode(out.stdout).decode() if out.returncode == 0 and out.stdout else ""


def store_secret(key: str, value: str) -> None:
    patch = json.dumps({"data": {key: base64.b64encode(value.encode()).decode()}})
    kubectl("-n", NAMESPACE, "patch", "secret", SECRET, "--type", "merge", "-p", patch)


class Gitea:
    """Gitea's API and CLI, inside its pod."""

    def __init__(self, pod: str = "deploy/gitea"):
        self.pod = pod

    def exec(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return kubectl(
            "-n", NAMESPACE, "exec", "-i", self.pod, "-c", "gitea", "--", *args, stdin=stdin, check=False
        )

    def api(
        self, method: str, path: str, body: object = None, *, token: str = "", basic: str = ""
    ) -> tuple[int, object]:
        auth = ["-H", f"Authorization: token {token}"] if token else ["-u", basic]
        out = self.exec(
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-X",
            method,
            *auth,
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            f"http://localhost:3000/api/v1{path}",
            stdin=json.dumps(body).encode() if body is not None else b"",
        )
        text, _, status = out.stdout.decode().rpartition("\n")
        try:
            return int(status), json.loads(text) if text.strip() else None
        except ValueError:
            return int(status or 0), text

    def healthy(self) -> bool:
        return self.exec("curl", "-sf", "http://localhost:3000/api/healthz").returncode == 0


def render(service: str) -> str:
    """The desired state of one sandbox deployment, as it runs now."""
    raw = kubectl("-n", SANDBOX, "get", "deploy", service, "-o", "json").stdout
    container = json.loads(raw)["spec"]["template"]["spec"]["containers"][0]
    lines = [
        f"# Desired state of sandbox/{service}: CD rolls out every change merged here.",
        f"service: {service}",
        f"image: {container['image']}",
        "env:",
    ]
    for env in sorted(container.get("env", []), key=lambda e: e["name"]):
        if "value" in env and not SECRET_ENV.search(env["name"]):
            lines.append(f"  {env['name']}: {json.dumps(env['value'])}")
    return "\n".join(lines) + "\n"


def bootstrap(gitea: Gitea) -> None:
    for _ in range(60):
        if gitea.healthy():
            break
        time.sleep(2)
    else:
        sys.exit("gitea is not healthy")
    password = secret_value("gitea-admin-password")
    if not password:
        sys.exit("relay-secrets has no gitea-admin-password: run `make relay-secrets` first")
    if ADMIN.encode() not in gitea.exec("gitea", "admin", "user", "list", "--admin").stdout:
        gitea.exec(
            "gitea",
            "admin",
            "user",
            "create",
            "--admin",
            "--username",
            ADMIN,
            "--password",
            password,
            "--email",
            "admin@shop.local",
            "--must-change-password=false",
        )
    admin = f"{ADMIN}:{password}"
    gitea.api("POST", "/orgs", {"username": "shop", "visibility": "public"}, basic=admin)
    gitea.api(
        "POST",
        "/orgs/shop/repos",
        {
            "name": "deploy",
            "default_branch": "main",
            "auto_init": True,
            "description": "Desired state of the sandbox services; CD applies what is merged here",
        },
        basic=admin,
    )
    restart = False
    for user, email, scopes, key in (
        ("ci-bot", "ci@shop.local", "write:repository", "gitea-ci-token"),
        ("relay-bot", "relay-bot@shop.local", "write:repository", "gitea-relay-token"),
    ):
        gitea.api(
            "POST",
            "/admin/users",
            {
                "username": user,
                "email": email,
                "password": secrets.token_urlsafe(24),
                "must_change_password": False,
            },
            basic=admin,
        )
        gitea.api("PUT", f"/repos/{REPO}/collaborators/{user}", {"permission": "write"}, basic=admin)
        if not secret_value(key):
            token = (
                gitea.exec(
                    "gitea",
                    "admin",
                    "user",
                    "generate-access-token",
                    "--username",
                    user,
                    "--token-name",
                    f"relay-{int(time.time())}",
                    "--scopes",
                    scopes,
                    "--raw",
                )
                .stdout.decode()
                .strip()
            )
            store_secret(key, token)
            restart = True
            print(f"  {key}: generated")
    gitea.api(
        "POST",
        f"/repos/{REPO}/branch_protections",
        {
            "rule_name": "main",
            "enable_push": True,
            "enable_push_whitelist": True,
            "push_whitelist_usernames": [ADMIN, "ci-bot"],
            "required_approvals": 1,
        },
        basic=admin,
    )
    seed = [
        {
            "operation": "create",
            "path": f"services/{s}.yaml",
            "content": base64.b64encode(render(s).encode()).decode(),
        }
        for s in SERVICES
        if gitea.api("GET", f"/repos/{REPO}/contents/services/{s}.yaml", basic=admin)[0] == 404
    ]
    if seed:
        status, _ = gitea.api(
            "POST",
            f"/repos/{REPO}/contents",
            {
                "message": "Seed the deploy repo from the running sandbox",
                "files": seed,
                "author": {"name": CI[0], "email": CI[1]},
            },
            token=secret_value("gitea-ci-token"),
        )
        print(f"  seeded {len(seed)} services: HTTP {status}")
    if restart:
        kubectl("-n", NAMESPACE, "rollout", "restart", "deploy/mcp-github", check=False)
    print(f"deploy repo ready: {REPO}")


def record(gitea: Gitea, service: str, message: str) -> None:
    """Commit the service's running state as CD would have, if it changed."""
    token = secret_value("gitea-ci-token")
    if not token:
        return  # no deploy repo in this cluster
    path = f"/repos/{REPO}/contents/services/{service}.yaml"
    status, current = gitea.api("GET", path, token=token)
    content = render(service)
    if status == 200 and isinstance(current, dict):
        if base64.b64decode(current["content"]).decode() == content:
            return
        body = {"sha": current["sha"]}
    else:
        body = {}
    body |= {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "author": {"name": CI[0], "email": CI[1]},
    }
    status, _ = gitea.api("PUT" if "sha" in body else "POST", path, body, token=token)
    if status >= 300:
        print(f"deploy repo: recording {service} failed (HTTP {status})", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("bootstrap", "record"):
        sys.exit(__doc__)
    gitea = Gitea()
    if kubectl("-n", NAMESPACE, "get", "deploy", "gitea", check=False).returncode != 0:
        return  # Relay is installed without its local Gitea
    if sys.argv[1] == "bootstrap":
        bootstrap(gitea)
    else:
        record(gitea, sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()

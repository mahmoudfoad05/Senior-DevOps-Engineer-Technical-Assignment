#!/usr/bin/env python3
"""
portainer_deploy.py — Task 3.2: Portainer API Automation Script

Authenticates with one or more Portainer endpoints, deploys/updates a Docker
Swarm stack from a local docker-compose.yml, and validates deployment health.
Uses only Python stdlib — no external dependencies required.

Usage:
    python3 portainer_deploy.py list
    python3 portainer_deploy.py deploy --stack myapp --compose docker-compose.yml --wait
    python3 portainer_deploy.py status --stack myapp
    python3 portainer_deploy.py delete --stack myapp

Environment variables (preferred over flags for secrets):
    PORTAINER_URL        https://portainer.example.com
    PORTAINER_TOKEN      pre-issued API access token (preferred)
    PORTAINER_USERNAME   admin
    PORTAINER_PASSWORD   secret
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, List
import urllib.request
import urllib.error
import urllib.parse

# ─── Constants ────────────────────────────────────────────────────────────────
DEFAULT_TIMEOUT      = 30    # seconds per HTTP request
DEPLOY_POLL_INTERVAL = 5     # seconds between deployment status polls
DEPLOY_MAX_WAIT      = 300   # max seconds to wait for all tasks to be running
DEFAULT_ENDPOINT     = "primary"


class PortainerClient:
    """Thin, stdlib-only wrapper around the Portainer REST API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None   # API access token (X-API-Key)
        self._jwt:   Optional[str] = None   # Session JWT (Authorization: Bearer)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(self, username: str, password: str) -> str:
        """Obtain a JWT via username/password. Returns the JWT."""
        payload = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/auth",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = self._send(req, authenticated=False)
        self._jwt = resp["jwt"]
        print(f"[AUTH] Authenticated as '{username}'")
        return self._jwt

    def set_api_token(self, token: str) -> None:
        """Use a pre-issued API access token (avoids password transmission)."""
        self._token = token

    # ------------------------------------------------------------------
    # Endpoints / Environments
    # ------------------------------------------------------------------
    def list_endpoints(self) -> list:
        return self._get("/api/endpoints")

    def get_endpoint_id(self, name: str = DEFAULT_ENDPOINT) -> int:
        """Resolve an endpoint name to its numeric ID."""
        endpoints = self.list_endpoints()
        # Exact name match (case-insensitive)
        for ep in endpoints:
            if ep.get("Name", "").lower() == name.lower():
                return ep["Id"]
        # Fallback: if exactly one endpoint exists, use it silently
        if len(endpoints) == 1:
            ep = endpoints[0]
            print(f"[WARN] Endpoint '{name}' not found; using sole endpoint '{ep['Name']}'")
            return ep["Id"]
        names = [e["Name"] for e in endpoints]
        raise ValueError(f"Endpoint '{name}' not found. Available: {names}")

    # ------------------------------------------------------------------
    # Stacks
    # ------------------------------------------------------------------
    def list_stacks(self, endpoint_id: Optional[int] = None) -> list:
        """Return all stacks, optionally filtered to a specific endpoint."""
        all_stacks = self._get("/api/stacks")
        if endpoint_id is not None:
            return [s for s in all_stacks if s.get("EndpointId") == endpoint_id]
        return all_stacks

    def get_stack(self, name: str, endpoint_id: int) -> Optional[dict]:
        """Find a stack by name on the given endpoint. Returns None if absent."""
        for stack in self.list_stacks(endpoint_id):
            if stack.get("Name") == name:
                return stack
        return None

    def deploy_stack(
        self,
        name: str,
        compose_file: Path,
        endpoint_id: int,
        env_vars: Optional[dict] = None,
    ) -> dict:
        """
        Create a new Swarm stack or update an existing one from a compose file.
        Uses Portainer API v2 endpoints — compatible with Portainer CE 2.x.
        """
        compose_content = compose_file.read_text()
        env_list = [{"name": k, "value": v} for k, v in (env_vars or {}).items()]

        existing = self.get_stack(name, endpoint_id)

        if existing:
            stack_id = existing["Id"]
            print(f"[DEPLOY] Updating stack '{name}' (id={stack_id})")
            payload = {
                "stackFileContent": compose_content,
                "env": env_list,
                "prune": True,    # Remove services no longer defined in the file
            }
            result = self._put(
                f"/api/stacks/{stack_id}",
                payload,
                params={"endpointId": endpoint_id},
            )
        else:
            print(f"[DEPLOY] Creating new Swarm stack '{name}'")
            swarm_id = self._get_swarm_id(endpoint_id)
            payload = {
                "name": name,
                "swarmID": swarm_id,
                "stackFileContent": compose_content,
                "env": env_list,
            }
            # Portainer CE 2.x create endpoint for Swarm stacks with string content
            result = self._post(
                "/api/stacks/create/swarm/string",
                payload,
                params={"endpointId": endpoint_id},
            )

        stack_id = result.get("Id", "unknown")
        print(f"[DEPLOY] Stack '{name}' submitted successfully (id={stack_id})")
        return result

    def wait_for_deployment(self, stack_name: str, endpoint_id: int) -> bool:
        """
        Poll service replicas until all reach their desired count or timeout.
        Returns True on success, False on timeout.
        """
        print(f"[WAIT] Polling deployment health for '{stack_name}' ...")
        deadline = time.monotonic() + DEPLOY_MAX_WAIT

        while time.monotonic() < deadline:
            stack = self.get_stack(stack_name, endpoint_id)
            if not stack:
                print("[WAIT]   Stack not yet visible — retrying ...")
                time.sleep(DEPLOY_POLL_INTERVAL)
                continue

            services = self._get_services_for_stack(stack_name, endpoint_id)
            if not services:
                print("[WAIT]   No services found yet — retrying ...")
                time.sleep(DEPLOY_POLL_INTERVAL)
                continue

            all_healthy = True
            for svc in services:
                name    = svc.get("Spec", {}).get("Name", "unknown")
                desired = (
                    svc.get("Spec", {})
                       .get("Mode", {})
                       .get("Replicated", {})
                       .get("Replicas", 1)
                )
                # Running task count comes from ServiceStatus (Portainer 2.13+)
                # or we fall back to querying tasks directly.
                running = self._get_running_task_count(svc, stack_name, endpoint_id)
                status  = "✓" if running >= desired else "…"
                print(f"[WAIT]   {status} {name}: {running}/{desired}")
                if running < desired:
                    all_healthy = False

            if all_healthy:
                print(f"[WAIT] All services healthy for stack '{stack_name}'")
                return True

            remaining = int(deadline - time.monotonic())
            print(f"[WAIT] Retrying in {DEPLOY_POLL_INTERVAL}s ({remaining}s remaining) ...")
            time.sleep(DEPLOY_POLL_INTERVAL)

        print(f"[ERROR] Deployment did not complete within {DEPLOY_MAX_WAIT}s")
        return False

    def delete_stack(self, name: str, endpoint_id: int) -> bool:
        stack = self.get_stack(name, endpoint_id)
        if not stack:
            print(f"[DELETE] Stack '{name}' not found.")
            return False
        self._delete(f"/api/stacks/{stack['Id']}", params={"endpointId": endpoint_id})
        print(f"[DELETE] Stack '{name}' removed.")
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_swarm_id(self, endpoint_id: int) -> str:
        """Return the Swarm cluster ID for a Portainer endpoint."""
        info = self._get(f"/api/endpoints/{endpoint_id}/docker/swarm")
        return info["ID"]

    def _get_services_for_stack(self, stack_name: str, endpoint_id: int) -> list:
        """
        Return Docker services belonging to the named stack.
        Filters by the com.docker.stack.namespace label which Swarm sets
        automatically for all services created by `docker stack deploy`.
        """
        # The filter must be JSON-encoded as a string per the Docker API spec
        filters = json.dumps({"label": [f"com.docker.stack.namespace={stack_name}"]})
        try:
            return self._get(
                f"/api/endpoints/{endpoint_id}/docker/services",
                params={"filters": filters},
            )
        except Exception:
            return []

    def _get_running_task_count(
        self, service: dict, stack_name: str, endpoint_id: int
    ) -> int:
        """
        Return number of running tasks for a service.
        Portainer 2.13+ populates ServiceStatus.RunningTasks; older versions do
        not — fall back to querying the tasks API directly in that case.
        """
        # Try Portainer's pre-computed field first (fast path)
        status = service.get("ServiceStatus")
        if status and "RunningTasks" in status:
            return int(status["RunningTasks"])

        # Slow path: count tasks from the Docker tasks API
        svc_id = service.get("ID", "")
        if not svc_id:
            return 0
        try:
            filters = json.dumps({"service": [svc_id], "desired-state": ["running"]})
            tasks = self._get(
                f"/api/endpoints/{endpoint_id}/docker/tasks",
                params={"filters": filters},
            )
            return sum(1 for t in tasks if t.get("Status", {}).get("State") == "running")
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # HTTP layer
    # ------------------------------------------------------------------
    def _auth_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-API-Key"] = self._token
        elif self._jwt:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    def _build_url(self, path: str, params: Optional[dict] = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _send(
        self,
        req: urllib.request.Request,
        authenticated: bool = True,
    ) -> any:
        if authenticated:
            for k, v in self._auth_headers().items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {exc.reason} — {body}") from exc

    def _get(self, path: str, params: Optional[dict] = None) -> any:
        return self._send(urllib.request.Request(self._build_url(path, params)))

    def _post(self, path: str, payload: dict, params: Optional[dict] = None) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._build_url(path, params), data=data, method="POST"
        )
        return self._send(req)

    def _put(self, path: str, payload: dict, params: Optional[dict] = None) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._build_url(path, params), data=data, method="PUT"
        )
        return self._send(req)

    def _delete(self, path: str, params: Optional[dict] = None) -> dict:
        req = urllib.request.Request(
            self._build_url(path, params), method="DELETE"
        )
        return self._send(req)


# ─── CLI helpers ──────────────────────────────────────────────────────────────

def build_client(args) -> PortainerClient:
    url = getattr(args, "url", None) or os.environ.get("PORTAINER_URL", "")
    if not url:
        sys.exit("[ERROR] --url or PORTAINER_URL is required")

    client = PortainerClient(url)

    token = getattr(args, "token", None) or os.environ.get("PORTAINER_TOKEN", "")
    if token:
        client.set_api_token(token)
    else:
        username = getattr(args, "username", None) or os.environ.get("PORTAINER_USERNAME", "")
        password = getattr(args, "password", None) or os.environ.get("PORTAINER_PASSWORD", "")
        if not (username and password):
            sys.exit(
                "[ERROR] Provide --token / PORTAINER_TOKEN  OR  "
                "--username + --password / PORTAINER_USERNAME + PORTAINER_PASSWORD"
            )
        client.authenticate(username, password)

    return client


def cmd_list(args):
    client = build_client(args)
    endpoint_id = client.get_endpoint_id(args.endpoint)
    stacks = client.list_stacks(endpoint_id)
    if not stacks:
        print("No stacks found.")
        return
    header = f"{'Name':<30} {'ID':<8} {'Status':<12} Created"
    print(f"\n{header}")
    print("-" * 70)
    for s in stacks:
        print(
            f"{s['Name']:<30} "
            f"{s['Id']:<8} "
            f"{s.get('Status', 'N/A'):<12} "
            f"{s.get('CreationDate', '')}"
        )


def cmd_deploy(args):
    client = build_client(args)
    endpoint_id = client.get_endpoint_id(args.endpoint)

    compose_path = Path(args.compose)
    if not compose_path.exists():
        sys.exit(f"[ERROR] Compose file not found: {compose_path}")

    env_vars: dict = {}
    for item in (args.env or []):
        if "=" not in item:
            sys.exit(f"[ERROR] Invalid --env value '{item}' — expected KEY=VALUE")
        k, v = item.split("=", 1)
        env_vars[k] = v

    client.deploy_stack(args.stack, compose_path, endpoint_id, env_vars)

    if args.wait:
        ok = client.wait_for_deployment(args.stack, endpoint_id)
        sys.exit(0 if ok else 1)


def cmd_status(args):
    client = build_client(args)
    endpoint_id = client.get_endpoint_id(args.endpoint)
    stack = client.get_stack(args.stack, endpoint_id)
    if not stack:
        print(f"Stack '{args.stack}' not found on endpoint '{args.endpoint}'")
        sys.exit(1)
    print(json.dumps(stack, indent=2))


def cmd_delete(args):
    client = build_client(args)
    endpoint_id = client.get_endpoint_id(args.endpoint)
    client.delete_stack(args.stack, endpoint_id)


# ─── Argument parser ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Portainer API automation — deploy/manage Docker Swarm stacks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url",      default=None, help="Portainer base URL (env: PORTAINER_URL)")
    parser.add_argument("--token",    default=None, help="API access token (env: PORTAINER_TOKEN)")
    parser.add_argument("--username", default=None, help="Username (env: PORTAINER_USERNAME)")
    parser.add_argument("--password", default=None, help="Password (env: PORTAINER_PASSWORD)")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Endpoint/environment name")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all stacks on the endpoint")

    p_deploy = sub.add_parser("deploy", help="Create or update a stack")
    p_deploy.add_argument("--stack",   required=True,          help="Stack name")
    p_deploy.add_argument("--compose", default="docker-compose.yml", help="Path to compose file")
    p_deploy.add_argument("--env",     nargs="*", metavar="KEY=VALUE", help="Environment variables")
    p_deploy.add_argument("--wait",    action="store_true",    help="Block until all services are healthy")

    p_status = sub.add_parser("status", help="Print stack JSON")
    p_status.add_argument("--stack", required=True)

    p_delete = sub.add_parser("delete", help="Remove a stack")
    p_delete.add_argument("--stack", required=True)

    args = parser.parse_args()
    {"list": cmd_list, "deploy": cmd_deploy, "status": cmd_status, "delete": cmd_delete}[args.command](args)


if __name__ == "__main__":
    main()

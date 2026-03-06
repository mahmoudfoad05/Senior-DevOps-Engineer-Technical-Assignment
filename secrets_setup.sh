#!/usr/bin/env bash
###############################################################################
# secrets_setup.sh — Task 1.3: Docker Swarm Secrets & Configs Management
#
# This script creates, rotates, and manages Swarm secrets and configs.
# Run on a Swarm manager node.
#
# Usage:
#   ./secrets_setup.sh create    — initial creation
#   ./secrets_setup.sh rotate    — zero-downtime secret rotation
#   ./secrets_setup.sh list      — list all secrets/configs
#   ./secrets_setup.sh delete    — remove all managed secrets/configs
###############################################################################

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
DB_USER="${DB_USER:-appuser}"
DB_NAME="${DB_NAME:-appdb}"
STACK_NAME="${STACK_NAME:-app}"

# Generate secure random passwords if not provided
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 32)}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(openssl rand -base64 32)}"
JWT_SECRET="${JWT_SECRET:-$(openssl rand -base64 64)}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-$(openssl rand -base64 24)}"

# ─── Helper functions ─────────────────────────────────────────────────────────
log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] INFO  $*"; }
warn() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN  $*" >&2; }
die()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR $*" >&2; exit 1; }

secret_exists() { docker secret ls --format '{{.Name}}' | grep -q "^${1}$"; }
config_exists() { docker config ls --format '{{.Name}}' | grep -q "^${1}$"; }

create_secret() {
  local name="$1"
  local value="$2"
  if secret_exists "$name"; then
    warn "Secret '$name' already exists — skipping (use rotate to update)"
    return
  fi
  printf '%s' "$value" | docker secret create "$name" -
  log "Created secret: $name"
}

###############################################################################
# create_all — provision all secrets and configs for the first time
###############################################################################
create_all() {
  log "=== Creating Swarm secrets ==="

  create_secret "db_username"         "$DB_USER"
  create_secret "db_password"         "$DB_PASSWORD"
  create_secret "db_connection_string" "postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}?sslmode=require"
  create_secret "redis_password"      "$REDIS_PASSWORD"
  create_secret "jwt_secret"          "$JWT_SECRET"
  create_secret "grafana_admin_password" "$GRAFANA_ADMIN_PASSWORD"

  log "=== Creating Swarm configs ==="

  # Application runtime config
  if ! config_exists "app_config"; then
    cat <<'EOF' | docker config create app_config -
{
  "featureFlags": {
    "newDashboard": false,
    "betaApi": false
  },
  "logLevel": "info",
  "maxRequestSize": "10mb"
}
EOF
    log "Created config: app_config"
  fi

  # Nginx config (if not using Traefik)
  if ! config_exists "nginx_config"; then
    cat <<'NGINX' | docker config create nginx_config -
worker_processes auto;
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    upstream frontend { server frontend:3000; }
    upstream backend  { server backend:4000; }

    server {
        listen 80;
        location /api/ { proxy_pass http://backend/; }
        location /     { proxy_pass http://frontend/; }
    }
}
NGINX
    log "Created config: nginx_config"
  fi

  log "=== Summary ==="
  log "DB Password stored in Swarm secret 'db_password' — save it:"
  log "  DB_PASSWORD = $DB_PASSWORD"
  log "  REDIS_PASSWORD = $REDIS_PASSWORD"
  log "  GRAFANA_ADMIN_PASSWORD = $GRAFANA_ADMIN_PASSWORD"
  warn "Save these values securely NOW — they cannot be retrieved from Swarm later!"
}

###############################################################################
# rotate_secret — zero-downtime secret rotation
#
# Strategy:
#   1. Create a NEW secret with a versioned name (e.g. db_password_v2)
#   2. Update the service definition to reference the new secret name
#      but mount it at the same target path (/run/secrets/db_password)
#   3. Swarm performs a rolling update, replacing replicas one by one
#   4. Once all replicas use the new secret, remove the old one
#
# This ensures no replica ever loses access to credentials during the rotation.
###############################################################################
rotate_secret() {
  local secret_name="${1:?Usage: rotate_secret <name> <new_value>}"
  local new_value="${2:?Usage: rotate_secret <name> <new_value>}"
  local timestamp
  timestamp="$(date +%s)"
  local new_name="${secret_name}_v${timestamp}"

  log "Rotating secret '$secret_name' → '$new_name'"

  # Step 1: Create new versioned secret
  printf '%s' "$new_value" | docker secret create "$new_name" -

  # Step 2: Update each service that uses this secret
  # This updates the service to use new_name but still mount at the old target path
  for service in $(docker service ls --format '{{.Name}}' --filter "label=com.docker.stack.namespace=${STACK_NAME}"); do
    local secrets
    secrets="$(docker service inspect "$service" --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{.SecretName}} {{end}}')"
    if echo "$secrets" | grep -qw "$secret_name"; then
      log "  Updating service: $service"
      docker service update \
        --secret-rm "$secret_name" \
        --secret-add "source=${new_name},target=${secret_name}" \
        "$service"
    fi
  done

  # Step 3: Remove old secret (after all services updated)
  log "Removing old secret: $secret_name"
  docker secret rm "$secret_name"

  # Step 4: Rename new secret to canonical name (via recreate — Swarm has no rename)
  # At this point services already reference new_name; optionally keep versioned name
  log "Secret rotation complete. New secret name: $new_name"
  log "Update docker-compose.yml to reference '$new_name' or redeploy with canonical name."
}

###############################################################################
# list — display all managed resources
###############################################################################
list_all() {
  log "=== Swarm Secrets ==="
  docker secret ls
  log "=== Swarm Configs ==="
  docker config ls
}

###############################################################################
# delete_all — clean up (USE ONLY IN DEV/STAGING)
###############################################################################
delete_all() {
  warn "This will DELETE all application secrets and configs!"
  read -rp "Type 'yes' to confirm: " confirm
  [[ "$confirm" == "yes" ]] || die "Aborted."

  for s in db_username db_password db_connection_string redis_password jwt_secret grafana_admin_password; do
    if secret_exists "$s"; then
      docker secret rm "$s" && log "Deleted secret: $s"
    fi
  done

  for c in app_config nginx_config; do
    if config_exists "$c"; then
      docker config rm "$c" && log "Deleted config: $c"
    fi
  done
}

###############################################################################
# Main dispatcher
###############################################################################
case "${1:-help}" in
  create)  create_all ;;
  rotate)  rotate_secret "${2:-}" "${3:-}" ;;
  list)    list_all ;;
  delete)  delete_all ;;
  *)
    echo "Usage: $0 {create|rotate <name> <value>|list|delete}"
    echo ""
    echo "  create              Create all secrets and configs (initial setup)"
    echo "  rotate <name> <val> Rotate a specific secret with zero downtime"
    echo "  list                List all secrets and configs"
    echo "  delete              Remove all managed secrets/configs (dev only)"
    exit 1
    ;;
esac

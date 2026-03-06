# Portainer Stack Management Guide — Task 3.1

## Installing Portainer CE on Docker Swarm

```bash
# Step 1: Create a dedicated Portainer overlay network
docker network create --driver overlay portainer_agent_network

# Step 2: Deploy Portainer Agent as a global service (one per node)
# The agent gives Portainer visibility into all nodes in the cluster
docker service create \
  --name portainer_agent \
  --network portainer_agent_network \
  --mode global \
  --constraint 'node.platform.os == linux' \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount type=bind,src=/var/lib/docker/volumes,dst=/var/lib/docker/volumes \
  portainer/agent:latest

# Step 3: Deploy Portainer CE Server (manager node only)
docker service create \
  --name portainer \
  --network portainer_agent_network \
  --publish mode=host,target=9000,published=9000 \
  --publish mode=host,target=9443,published=9443 \
  --constraint 'node.role == manager' \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount type=volume,src=portainer_data,dst=/data \
  portainer/portainer-ce:latest

# Step 4: Access Portainer UI
# https://<MANAGER-IP>:9443
# Create admin account on first visit (within 5 minutes or it locks)
```

---

## Deploying a Stack via Portainer UI

1. Navigate to **Stacks** → **Add stack**
2. Name the stack (e.g., `app`)
3. Choose **Upload** and select your `docker-compose.yml`
4. Under **Environment variables**, add:
   - `FRONTEND_TAG` = `v1.0.0`
   - `BACKEND_TAG` = `v1.0.0`
5. Click **Deploy the stack**
6. Monitor deployment progress in **Stacks** → **app** → **Services**

---

## Deploying a Stack via Portainer API (curl examples)

```bash
# Variables
PORTAINER_URL="https://portainer.example.com"
USERNAME="admin"
PASSWORD="your-password"

# Step 1: Authenticate and obtain JWT
TOKEN=$(curl -s -X POST "${PORTAINER_URL}/api/auth" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['jwt'])")

echo "JWT: ${TOKEN}"

# Step 2: List endpoints (environments)
curl -s -H "Authorization: Bearer ${TOKEN}" \
  "${PORTAINER_URL}/api/endpoints" | python3 -m json.tool

# Step 3: Get Swarm ID for the endpoint (required for stack creation)
ENDPOINT_ID=1  # From step 2
SWARM_ID=$(curl -s -H "Authorization: Bearer ${TOKEN}" \
  "${PORTAINER_URL}/api/endpoints/${ENDPOINT_ID}/docker/swarm" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['ID'])")

echo "Swarm ID: ${SWARM_ID}"

# Step 4: Deploy the stack
STACK_FILE_CONTENT=$(cat docker-compose.yml)

curl -s -X POST "${PORTAINER_URL}/api/stacks/create/swarm/string?endpointId=${ENDPOINT_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"app\",
    \"swarmID\": \"${SWARM_ID}\",
    \"stackFileContent\": $(echo "$STACK_FILE_CONTENT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),
    \"env\": [
      {\"name\": \"FRONTEND_TAG\", \"value\": \"v1.0.0\"},
      {\"name\": \"BACKEND_TAG\", \"value\": \"v1.0.0\"}
    ]
  }" | python3 -m json.tool

# Step 5: Update an existing stack
STACK_ID=1  # From list stacks response
curl -s -X PUT "${PORTAINER_URL}/api/stacks/${STACK_ID}?endpointId=${ENDPOINT_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"stackFileContent\": $(echo "$STACK_FILE_CONTENT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),
    \"env\": [
      {\"name\": \"FRONTEND_TAG\", \"value\": \"v1.1.0\"}
    ],
    \"prune\": true
  }" | python3 -m json.tool

# Use the Python script for full automation:
python3 scripts/portainer_deploy.py deploy \
  --url "${PORTAINER_URL}" \
  --token "your-api-token" \
  --stack app \
  --compose docker-compose.yml \
  --env "FRONTEND_TAG=v1.0.0" "BACKEND_TAG=v1.0.0" \
  --wait
```

---

## Portainer Stack Organization Best Practices

| Practice | Recommendation |
|----------|---------------|
| **Naming** | Use `<env>-<app>` pattern: `prod-app`, `staging-app`, `prod-monitoring` |
| **Tagging** | Tag stacks with team, cost center, and criticality labels |
| **Environments** | Create separate Portainer environments for prod vs staging (different endpoints) |
| **Access control** | Create teams in Portainer and assign stack ownership to teams |
| **API tokens** | Use service-specific API tokens (not admin password) for CI/CD automation |
| **Templates** | Save your docker-compose.yml as a Portainer App Template for repeatable deployments |

---

## Creating a Portainer App Template

1. Go to **Settings** → **App Templates** → **Edit templates**
2. Add your stack as a custom template:

```json
{
  "version": "2",
  "templates": [
    {
      "type": 2,
      "title": "App Stack",
      "description": "Full-stack microservices: Next.js + Node.js + PostgreSQL + Redis + Traefik",
      "categories": ["production", "microservices"],
      "platform": "linux",
      "logo": "https://example.com/logo.png",
      "repository": {
        "url": "https://github.com/yourorg/devops-assignment",
        "stackfile": "docker-compose.yml"
      },
      "env": [
        {
          "name": "FRONTEND_TAG",
          "label": "Frontend Image Tag",
          "description": "Docker image tag for the frontend service",
          "default": "latest"
        },
        {
          "name": "BACKEND_TAG",
          "label": "Backend Image Tag",
          "default": "latest"
        }
      ]
    }
  ]
}
```

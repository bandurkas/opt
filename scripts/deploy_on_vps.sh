#!/bin/bash
# Deploy pre-built docker images on VPS
# Run AFTER transferring tar files: scp docker_images/*.tar root@VPS:/root/opt-app/docker_images/

set -e

cd /root/opt-app

echo "=== VPS Deployer ==="
echo ""

# 0. Cleanup old containers and images
echo "[0/6] Cleaning up old docker artifacts..."
echo "  Stopping all containers..."
docker compose down --remove-orphans 2>/dev/null || true

echo "  Removing all stopped containers..."
docker container prune -f --filter "until=168h" 2>/dev/null || true

echo "  Removing all old images..."
docker image prune -af 2>/dev/null || true

echo "  Removing dangling volumes..."
docker volume prune -f 2>/dev/null || true

echo "  Listing remaining images..."
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

echo "  Disk usage before cleanup:"
df -h / | tail -1

# 1. Load images
echo ""
echo "[1/6] Loading docker images..."
docker load -i docker_images/backend.tar
docker load -i docker_images/frontend.tar
docker load -i docker_images/postgres.tar
docker load -i docker_images/redis.tar

# 2. Pull latest code
echo ""
echo "[2/6] Pulling latest code..."
git pull origin main

# 3. Start new containers
echo ""
echo "[3/6] Starting new containers..."

# 4. Wait for startup
echo ""
echo "[4/6] Waiting for services to start..."
sleep 5

# 5. Verify
echo ""
echo "[5/6] Verifying..."
echo ""
echo "=== Container Status ==="
docker compose ps
echo ""

echo "=== Backend Logs ==="
docker compose logs --tail 5 backend
echo ""

echo "=== Paper Logs ==="
docker compose logs --tail 5 paper
echo ""

echo "=== API Check ==="
curl -s http://localhost:8000/api/v1/paper/state | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8000/api/v1/paper/state
echo ""
curl -s http://localhost:8000/api/v1/paper/conditions | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8000/api/v1/paper/conditions
echo ""

# 6. Final disk check
echo ""
echo "[6/6] Final disk usage:"
df -h / | tail -1
docker system df

echo "=== DONE ==="
echo "Frontend: http://187.127.114.34:3000"
echo "API:      http://187.127.114.34:8000"

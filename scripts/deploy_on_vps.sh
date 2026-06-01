#!/bin/bash
# Deploy pre-built docker images on VPS
# Run AFTER transferring tar files: scp docker_images/*.tar root@VPS:/root/opt-app/docker_images/

set -e

cd /root/opt-app

echo "=== VPS Deployer ==="
echo ""

# 1. Load backend image
echo ""
echo "[1/7] Loading backend docker image..."
docker load -i docker_images/backend.tar

# 2. Pull latest code
echo ""
echo "[2/7] Pulling latest code..."
git pull origin main

# 3. Build frontend natively on x86_64 (avoid qemu SIGSEGV)
echo ""
echo "[3/7] Building frontend image natively on VPS..."
docker compose build frontend

# 4. Stop old containers and cleanup
echo ""
echo "[4/7] Cleaning up old docker artifacts..."
echo "  Stopping all containers..."
docker compose down --remove-orphans 2>/dev/null || true

echo "  Removing old images..."
docker image prune -af --filter "until=168h" 2>/dev/null || true

echo "  Removing dangling volumes..."
docker volume prune -f 2>/dev/null || true

# 5. Start new containers
echo ""
echo "[5/7] Starting new containers..."
docker compose up -d

# 6. Wait for startup and verify
echo ""
echo "[6/7] Waiting for services to start..."
sleep 5

echo ""
echo "[7/7] Verifying..."
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

# Final disk check
echo ""
echo "=== Disk usage ==="
df -h / | tail -1
docker system df

echo "=== DONE ==="
echo "Frontend: http://187.127.114.34:3000"
echo "API:      http://187.127.114.34:8000"

#!/bin/bash
# Build docker images for linux/amd64 (VPS compatible) and save as tar files.
# Run from project root: /Users/sabar/Desktop/options
# Usage: bash scripts/build_for_vps.sh

set -e

cd "$(dirname "$0")/.."

echo "=== VPS Docker Image Builder ==="
echo "Building for: linux/amd64 (x86_64)"
echo "Project dir: $(pwd)"
echo ""

# 1. Clean old images
echo "[1/4] Cleaning old build artifacts..."
rm -rf frontend/.next
rm -rf docker_images
mkdir -p docker_images

# 2. Build images
echo "[2/4] Building docker images..."
docker compose build --platform linux/amd64

# 3. Save images
echo ""
echo "[3/4] Saving images to tar files..."
echo "This may take a few minutes..."

docker save opt-app-backend:latest     -o docker_images/backend.tar     2>/dev/null || \
  docker save "$(docker images --format '{{.Repository}}:{{.Tag}}' | grep opt-app-backend | head -1)" \
    -o docker_images/backend.tar

docker save opt-app-frontend:latest    -o docker_images/frontend.tar    2>/dev/null || \
  docker save "$(docker images --format '{{.Repository}}:{{.Tag}}' | grep opt-app-frontend | head -1)" \
    -o docker_images/frontend.tar

docker save postgres:15-alpine         -o docker_images/postgres.tar
docker save redis:alpine               -o docker_images/redis.tar

# 4. Show sizes
echo ""
echo "[4/4] Image sizes:"
ls -lh docker_images/

TOTAL=$(du -sh docker_images/ | cut -f1)
echo ""
echo "Total: ${TOTAL}"
echo ""
echo "=== DONE ==="
echo ""
echo "Transfer to VPS:"
echo "  scp docker_images/*.tar root@187.127.114.34:/root/opt-app/docker_images/"
echo ""
echo "On VPS:"
echo "  ssh root@187.127.114.34"
echo "  cd /root/opt-app"
echo "  git pull origin main"
echo "  docker load -i docker_images/backend.tar"
echo "  docker load -i docker_images/frontend.tar"
echo "  docker compose down"
echo "  docker compose up -d"

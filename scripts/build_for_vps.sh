#!/bin/bash
# Build ONLY backend image for linux/amd64 (VPS compatible) and save as tar.
# IMPORTANT: frontend is NOT built here — next build under QEMU emulation
# on Apple Silicon crashes with SIGSEGV. Frontend is built natively on VPS
# during deploy (see deploy_on_vps.sh).
#
# Run from project root: bash scripts/build_for_vps.sh

set -e

cd "$(dirname "$0")/.."

echo "=== VPS Backend Image Builder ==="
echo "Building backend for: linux/amd64 (x86_64)"
echo "Project dir: $(pwd)"
echo ""

# 1. Clean old artifacts
echo "[1/4] Cleaning old build artifacts..."
rm -rf docker_images
mkdir -p docker_images

# 2. Build backend only with buildx for cross-platform
echo "[2/4] Building backend docker image for linux/amd64..."
echo "  (frontend will be built natively on VPS)"
echo ""

DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build backend

# 3. Save backend image
echo ""
echo "[3/4] Saving backend image to tar..."
docker save "$(docker images --format '{{.Repository}}:{{.Tag}}' | grep opt-app-backend | head -1)" \
  -o docker_images/backend.tar

# Also save base images (already arm64, but VPS needs them)
# These are small and commonly available, so we skip saving them
# to save transfer time. They'll be pulled on VPS if missing.

# 4. Show sizes
echo ""
echo "[4/4] Image sizes:"
ls -lh docker_images/

TOTAL=$(du -sh docker_images/ | cut -f1)
echo ""
echo "Backend image: ${TOTAL}"
echo ""
echo "=== DONE ==="
echo ""
echo "Transfer to VPS:"
echo "  scp docker_images/backend.tar root@187.127.114.34:/root/opt-app/docker_images/"
echo ""
echo "On VPS:"
echo "  ssh root@187.127.114.34"
echo "  cd /root/opt-app"
echo "  git pull origin main"
echo "  docker compose build frontend    # builds natively on x86_64"
echo "  docker compose up -d"

#!/bin/bash
# Deploy pre-built docker images on VPS
# Run AFTER transferring tar files: scp docker_images/*.tar root@VPS:/root/opt-app/docker_images/

set -e

cd /root/opt-app

echo "=== VPS Deployer ==="
echo ""

# 1. Load backend image
echo ""
echo "[1/8] Loading backend docker image..."
docker load -i docker_images/backend.tar

# 2. Pull latest code
echo ""
echo "[2/8] Pulling latest code..."
git pull origin main

# 3. Build frontend natively on x86_64 (avoid qemu SIGSEGV)
echo ""
echo "[3/8] Building frontend image natively on VPS..."
docker compose build frontend

# 4. Guard: refuse to take trading containers down while positions are open
# (2026-06-23 incident: redeploy killed eth_straddle_paper for ~2h45m while a
# Put leg was past its SL trigger, turning a $5.71 intended stop into -$24.58)
echo ""
echo "[4/8] Checking for open positions before taking containers down..."
OPEN_POSITIONS=$(docker exec opt-app-postgres-1 psql -U user -d options_assistant -t -A -c "
  SELECT 'eth_straddle' AS bot, id, leg, opened_at_ms FROM eth_straddle_positions WHERE status='open'
  UNION ALL
  SELECT 'btc_straddle' AS bot, id, leg, opened_at_ms FROM btc_straddle_positions WHERE status='open'
  UNION ALL
  SELECT 'paper' AS bot, id, NULL, opened_at_ms FROM paper_positions WHERE status='open';
")
if [ -n "$OPEN_POSITIONS" ]; then
  echo "  ABORT: open positions found, refusing to stop trading containers:"
  echo "$OPEN_POSITIONS"
  echo "  Wait for the cycle to close (or close manually via Mission Control), then re-run deploy."
  echo "  Override only if you understand the risk: DEPLOY_FORCE=1 ./deploy_on_vps.sh"
  if [ -z "$DEPLOY_FORCE" ]; then
    exit 1
  fi
  echo "  DEPLOY_FORCE=1 set — proceeding anyway."
else
  echo "  No open positions. Safe to proceed."
fi

# 5. Stop old containers and cleanup
echo ""
echo "[5/8] Cleaning up old docker artifacts..."
echo "  Stopping all containers..."
docker compose down --remove-orphans 2>/dev/null || true

echo "  Removing old images..."
docker image prune -af --filter "until=168h" 2>/dev/null || true

echo "  Removing dangling volumes..."
docker volume prune -f 2>/dev/null || true

# 6. Start new containers
echo ""
echo "[6/8] Starting new containers..."
docker compose up -d

# 7. Wait for startup and verify
echo ""
echo "[7/8] Waiting for services to start..."
sleep 5

echo ""
echo "[8/8] Verifying..."
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

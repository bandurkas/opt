#!/bin/bash

# Grogu1 Charts Deployment Script
# Deploys backend API endpoint to VPS
# Usage: bash scripts/deploy_grogu_charts_vps.sh

set -e

VPS_HOST="187.127.114.34"
VPS_USER="root"
VPS_PATH="/root/opt-app"
LOCAL_BACKEND="docs/GROGU_POSITIONS_BACKEND.py"

echo "🚀 GROGU1 CHARTS DEPLOYMENT — STARTING"
echo "========================================"
echo ""

# Step 1: Verify local files exist
echo "📋 Step 1: Checking local files..."
if [ ! -f "$LOCAL_BACKEND" ]; then
    echo "❌ Error: $LOCAL_BACKEND not found"
    exit 1
fi
echo "✅ Backend file found: $LOCAL_BACKEND"
echo ""

# Step 2: Copy backend to VPS
echo "📋 Step 2: Copying backend to VPS..."
echo "   scp $LOCAL_BACKEND $VPS_USER@$VPS_HOST:$VPS_PATH/api/grogu_positions.py"

scp -o ConnectTimeout=10 "$LOCAL_BACKEND" "$VPS_USER@$VPS_HOST:$VPS_PATH/api/grogu_positions.py" \
    || {
        echo "❌ SCP failed. Check VPS is online and SSH key configured."
        exit 1
    }

echo "✅ Copied to VPS"
echo ""

# Step 3: Verify file on VPS
echo "📋 Step 3: Verifying file on VPS..."
ssh -o ConnectTimeout=10 "$VPS_USER@$VPS_HOST" "ls -lh $VPS_PATH/api/grogu_positions.py" \
    || {
        echo "❌ File not found on VPS"
        exit 1
    }
echo "✅ File verified on VPS"
echo ""

# Step 4: Check Python syntax
echo "📋 Step 4: Checking Python syntax..."
ssh "$VPS_USER@$VPS_HOST" "python3 -m py_compile $VPS_PATH/api/grogu_positions.py" \
    || {
        echo "❌ Syntax error in Python file"
        exit 1
    }
echo "✅ Python syntax valid"
echo ""

# Step 5: Next steps
echo "📋 Step 5: Next steps..."
echo ""
echo "✅ BACKEND FILE DEPLOYED"
echo ""
echo "Now you need to:"
echo "1. SSH to VPS:"
echo "   ssh root@$VPS_HOST"
echo ""
echo "2. Add import to /root/opt-app/main.py:"
echo "   from api.grogu_positions import app as grogu_app"
echo ""
echo "3. Add route (inside app initialization):"
echo "   app.include_router(grogu_app.router)"
echo ""
echo "4. Add CORS middleware:"
echo "   from fastapi.middleware.cors import CORSMiddleware"
echo "   app.add_middleware("
echo "       CORSMiddleware,"
echo "       allow_origins=['*'],"
echo "       allow_credentials=True,"
echo "       allow_methods=['*'],"
echo "       allow_headers=['*'],"
echo "   )"
echo ""
echo "5. Restart FastAPI app:"
echo "   docker compose restart <service-name>"
echo ""
echo "6. Test endpoint:"
echo "   curl 'http://$VPS_HOST:8000/api/v1/grogu/positions?with_levels=true'"
echo ""
echo "========================================"
echo "✅ DEPLOYMENT SCRIPT COMPLETE"

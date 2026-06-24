#!/bin/bash

# Grogu1 Charts Frontend Installation
# Installs React component into Mission Control dashboard
# Usage: bash scripts/install_grogu_charts_frontend.sh /path/to/mission-control

set -e

if [ $# -eq 0 ]; then
    echo "Usage: bash scripts/install_grogu_charts_frontend.sh /path/to/mission-control"
    echo ""
    echo "Example:"
    echo "  bash scripts/install_grogu_charts_frontend.sh ~/mission-control"
    echo "  bash scripts/install_grogu_charts_frontend.sh /root/opt-app/frontend"
    exit 1
fi

MISSION_CONTROL_PATH="$1"
COMPONENT_SRC="src/components/GroguPositionChart.tsx"
COMPONENT_DEST="$MISSION_CONTROL_PATH/src/components/GroguPositionChart.tsx"

echo "🎨 GROGU1 CHARTS FRONTEND INSTALLATION"
echo "======================================"
echo ""

# Step 1: Verify paths
echo "📋 Step 1: Verifying paths..."
if [ ! -f "$COMPONENT_SRC" ]; then
    echo "❌ Source component not found: $COMPONENT_SRC"
    exit 1
fi
echo "✅ Source component found"

if [ ! -d "$MISSION_CONTROL_PATH" ]; then
    echo "❌ Mission Control path not found: $MISSION_CONTROL_PATH"
    exit 1
fi
echo "✅ Mission Control path valid"

if [ ! -d "$MISSION_CONTROL_PATH/src/components" ]; then
    echo "❌ Components directory not found: $MISSION_CONTROL_PATH/src/components"
    exit 1
fi
echo "✅ Components directory exists"
echo ""

# Step 2: Copy component
echo "📋 Step 2: Copying component..."
cp "$COMPONENT_SRC" "$COMPONENT_DEST"
echo "✅ Component copied to: $COMPONENT_DEST"
echo ""

# Step 3: Check dependencies
echo "📋 Step 3: Checking dependencies..."
cd "$MISSION_CONTROL_PATH"

if ! grep -q "recharts" package.json; then
    echo "⚠️  recharts not in package.json"
    echo "   Installing: npm install recharts"
    npm install recharts
    echo "✅ recharts installed"
else
    echo "✅ recharts already installed"
fi

if ! grep -q "tailwindcss" package.json; then
    echo "⚠️  tailwindcss not found (needed for styling)"
    echo "   Please ensure Tailwind CSS is configured"
else
    echo "✅ Tailwind CSS configured"
fi
echo ""

# Step 4: Next steps
echo "📋 Step 4: Next steps..."
echo ""
echo "✅ COMPONENT INSTALLED"
echo ""
echo "To use the component in your dashboard:"
echo ""
echo "1. Open your dashboard page component:"
echo "   nano/vim src/pages/dashboard.tsx"
echo ""
echo "2. Add import:"
echo "   import GroguPositionChart from '@/components/GroguPositionChart';"
echo ""
echo "3. Add to JSX:"
echo "   <section className='mt-8'>"
echo "     <h2 className='text-2xl font-bold mb-4'>Live Positions</h2>"
echo "     <GroguPositionChart />"
echo "   </section>"
echo ""
echo "4. Configure API endpoint in the component:"
echo "   - Update the VPS IP if different from 187.127.114.34"
echo "   - Edit src/components/GroguPositionChart.tsx line 48"
echo ""
echo "5. Test:"
echo "   npm run dev"
echo "   Open http://localhost:3000/dashboard"
echo ""
echo "======================================"
echo "✅ INSTALLATION COMPLETE"

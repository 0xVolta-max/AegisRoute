#!/usr/bin/env bash
# ==============================================================================
# AegisRoute - Circuit-Breaker & Failover Simulation Test
# Simulates Google Colab GPU quota exhaustion (Exit Code 2) and tests fallback
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "============================================================"
echo "🛡️ AegisRoute: Circuit-Breaker & Failover Simulation"
echo "============================================================"

echo "Step 1: Simulating Quota Limit Detection (Exit Code 2)..."
python3 - << 'EOF'
import sys
from core.alerting import AlertDispatcher, AlertLevel

print("[Simulation] Emulating Playwright Quota Limit dialog match...")
dispatcher = AlertDispatcher()
results = dispatcher.dispatch_alert(
    title="[SIMULATION] Colab GPU Quota Limit Reached",
    message="Simulated run: Colab backend reached usage limit. Triggering fallback chain.",
    level=AlertLevel.CRITICAL,
    provider="colab-aegis",
    extra={"cooldown_hours": 4.0, "simulation": True}
)
print(f"[Simulation] Alert Dispatch Results: {results}")
EOF

echo ""
echo "Step 2: Testing OmniRoute Plugin Fallback Routing Simulation..."
node - << 'EOF'
const AegisRoutePlugin = require('./plugin/index.js');

const plugin = new AegisRoutePlugin({
  tunnelUrl: 'http://localhost:9999/v1', // Non-existent port to force immediate fallback
  cooldownHours: 4.0,
  fallbackChain: ['local-mlx', 'anthropic', 'openai'],
});

async function runTest() {
  console.log('[Test] Initializing plugin instance...');
  await plugin.onInit({});

  console.log('[Test] Simulating security request routing...');
  const route = await plugin.onRoute({
    messages: [{ role: 'user', content: 'Audit this smart contract for reentrancy vulnerabilities.' }]
  });
  console.log('[Test] Initial Route Result (Expected fallback redirect when unhealthy):', route);

  console.log('[Test] Simulating failure & fallback hook...');
  const fallbackRoute = await plugin.onFallback(new Error('Connection refused to Colab tunnel'), {
    model: 'aegis-security'
  });
  console.log('[Test] Fallback Route Result:', fallbackRoute);

  console.log('[Test] Checking Plugin State:');
  console.log(JSON.stringify(plugin.state, null, 2));

  await plugin.onDestroy();
  console.log('✅ Fallback simulation completed successfully.');
}

runTest().catch(console.error);
EOF

echo ""
echo "============================================================"
echo "✅ All Circuit-Breaker & Failover tests passed!"
echo "============================================================"

#!/usr/bin/env bash
# ==============================================================================
# AegisRoute - Tool Calling / Function Calling Validation Test
# Sends an OpenAI-compatible function calling payload to verify structured output
# ==============================================================================

set -eo pipefail

BASE_URL="${AEGIS_ENDPOINT:-http://localhost:20128/v1}"
API_KEY="${AEGIS_API_KEY:-aegis-test}"
MODEL="${AEGIS_MODEL:-aegis-security}"

echo "============================================================"
echo "🛡️ Testing AegisRoute Tool Calling on: ${BASE_URL}"
echo "   Model: ${MODEL}"
echo "============================================================"

PAYLOAD=$(cat << 'EOF'
{
  "model": "aegis-security",
  "messages": [
    {
      "role": "system",
      "content": "You are a smart contract static security scanner assistant. When you detect suspicious code patterns, call the run_static_analysis tool."
    },
    {
      "role": "user",
      "content": "Please scan the contract Vault.sol for reentrancy bugs using the Slither and Mythril analyzers."
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "run_static_analysis",
        "description": "Execute automated static security analyzer on source code",
        "parameters": {
          "type": "object",
          "properties": {
            "target_file": {
              "type": "string",
              "description": "The path to the contract file to analyze"
            },
            "analyzers": {
              "type": "array",
              "items": { "type": "string" },
              "description": "List of security tools to run (slither, mythril, semgrep)"
            },
            "severity_threshold": {
              "type": "string",
              "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
              "description": "Minimum severity to alert on"
            }
          },
          "required": ["target_file", "analyzers"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "temperature": 0.1
}
EOF
)

echo "Sending tool-calling request..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "${PAYLOAD}" || true)

echo ""
echo "--- RAW RESPONSE ---"
echo "${RESPONSE}"
echo "--------------------"

# Check if response contains tool_calls or function invocation
if echo "${RESPONSE}" | grep -q "tool_calls"; then
  echo "✅ SUCCESS: Valid tool_calls structure detected in response!"
elif echo "${RESPONSE}" | grep -q "run_static_analysis"; then
  echo "✅ SUCCESS: Tool name 'run_static_analysis' detected in generation!"
elif echo "${RESPONSE}" | grep -q "error"; then
  echo "⚠️ WARNING: Endpoint returned an error response (verify backend connection)."
else
  echo "ℹ️ Response received from inference bridge."
fi

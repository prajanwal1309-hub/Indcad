#!/usr/bin/env bash
API="http://localhost:5001"
echo "Health:"
curl -i $API/health
echo
echo "Lookup by title:"
curl -s -X POST $API/lookup-by-title -H "Content-Type: application/json" -d '{"title":"Conference and event planners","k":3}' | jq
echo
echo "Match by duties:"
curl -s -X POST $API/match-noc -H "Content-Type: application/json" -d '{"query":"plan and coordinate events, manage vendors, prepare budgets","k":3}' | jq

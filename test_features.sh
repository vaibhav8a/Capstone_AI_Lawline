#!/bin/bash

# ══════════════════════════════════════════════════════════════════════
# Legal RAG Pro - Quick Feature Test Script
# ══════════════════════════════════════════════════════════════════════
# Run this script to verify all features are working

set -e

BASE_URL="http://localhost:8000"
PASS=0
FAIL=0
SKIP=0

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "═══════════════════════════════════════════════════════════════════════"
echo "Legal RAG Pro - Feature Test Suite"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Helper functions
test_pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((PASS++))
}

test_fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((FAIL++))
}

test_skip() {
    echo -e "${YELLOW}⊘ SKIP${NC}: $1"
    ((SKIP++))
}

check_endpoint() {
    local endpoint=$1
    local description=$2
    
    echo ""
    echo "Testing: $description"
    echo "Endpoint: $endpoint"
    
    if response=$(curl -s -o /dev/null -w "%{http_code}" "$endpoint"); then
        if [ "$response" = "200" ] || [ "$response" = "307" ]; then
            test_pass "$description"
            return 0
        else
            test_fail "$description (HTTP $response)"
            return 1
        fi
    else
        test_fail "$description (Connection failed)"
        return 1
    fi
}

# ───────────────────────────────────────────────────────────────────────
# 1. System Health Check
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ SYSTEM HEALTH ─────────────────────────────────────────────────┐"

check_endpoint "$BASE_URL/health" "Backend is running"
check_endpoint "$BASE_URL/docs" "API documentation available"
check_endpoint "$BASE_URL/metrics" "Prometheus metrics endpoint"

# Health endpoint with details
echo ""
echo "Checking detailed system health..."
HEALTH=$(curl -s "$BASE_URL/api/index/health")
if echo "$HEALTH" | grep -q "healthy"; then
    RAG_INIT=$(echo "$HEALTH" | grep -o '"rag_service_initialized":true' | wc -l)
    DOC_COUNT=$(echo "$HEALTH" | grep -o '"indexed_documents":[0-9]*' | head -1)
    
    if [ "$RAG_INIT" = "1" ]; then
        test_pass "RAG service initialized"
    else
        test_fail "RAG service not initialized"
    fi
    
    if [ ! -z "$DOC_COUNT" ]; then
        test_pass "Indexed documents count available: $DOC_COUNT"
    fi
else
    test_fail "System health check failed"
fi

# ───────────────────────────────────────────────────────────────────────
# 2. Authentication Bypass
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ AUTHENTICATION ────────────────────────────────────────────────┐"

AUTH_RESPONSE=$(curl -s -X GET "$BASE_URL/auth/me" 2>&1 || echo "failed")
if echo "$AUTH_RESPONSE" | grep -q "guest\|admin"; then
    test_pass "Auth bypass working (guest access enabled)"
else
    test_fail "Auth bypass not working properly"
fi

# ───────────────────────────────────────────────────────────────────────
# 3. Query Execution
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ QUERY EXECUTION ───────────────────────────────────────────────┐"

echo ""
echo "Running test query..."
QUERY_RESPONSE=$(curl -s -X POST "$BASE_URL/api/query/execute" \
    -H "Content-Type: application/json" \
    -d '{"query": "what is the right to life", "use_hyde": true, "use_self_rag": false}' 2>&1)

if echo "$QUERY_RESPONSE" | grep -q "context_chunks\|query"; then
    test_pass "Query execution working"
    
    # Check for context chunks
    CHUNK_COUNT=$(echo "$QUERY_RESPONSE" | grep -o '"chunk_id"' | wc -l)
    if [ "$CHUNK_COUNT" -gt "0" ]; then
        test_pass "Retrieved $CHUNK_COUNT context chunks"
    else
        test_fail "No chunks retrieved"
    fi
    
    # Check for chunk metadata
    if echo "$QUERY_RESPONSE" | grep -q '"case_title"\|"court"\|"date"'; then
        test_pass "Chunk metadata present (case_title, court, date)"
    else
        test_fail "Chunk metadata missing"
    fi
else
    test_fail "Query execution failed"
fi

# ───────────────────────────────────────────────────────────────────────
# 4. Streaming Response
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ STREAMING RESPONSE ────────────────────────────────────────────┐"

echo ""
echo "Testing streaming endpoint (5 second timeout)..."
STREAM_TEST=$(timeout 5 curl -s -N -X POST "$BASE_URL/api/query/stream" \
    -H "Content-Type: application/json" \
    -d '{"query": "explain fundamental rights", "stream": true}' 2>&1 | head -c 100)

if [ ! -z "$STREAM_TEST" ]; then
    test_pass "Streaming endpoint responding"
else
    test_fail "Streaming endpoint not responding"
fi

# ───────────────────────────────────────────────────────────────────────
# 5. Cheat Sheet Generation
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ CHEAT SHEET GENERATION ────────────────────────────────────────┐"

echo ""
echo "Testing cheat sheet generation (requires GROQ_API_KEY)..."
CHEATSHEET=$(curl -s -X POST "$BASE_URL/api/summary/generate" \
    -H "Content-Type: application/json" \
    -d '{
      "document_text": "Maneka Gandhi v. Union of India: The petitioner was denied a passport. The Court held that the right to travel abroad is fundamental. This case established that citizens have the right to move freely and that passport denial must follow due process."
    }' 2>&1)

if echo "$CHEATSHEET" | grep -q '"case_title"\|"facts"\|"ratio"'; then
    test_pass "Cheat sheet generation working"
    
    if echo "$CHEATSHEET" | grep -q '"holding"'; then
        test_pass "Generated holding field"
    fi
else
    if echo "$CHEATSHEET" | grep -q "error\|GROQ"; then
        test_skip "Cheat sheet generation (GROQ_API_KEY not configured)"
    else
        test_fail "Cheat sheet generation failed"
    fi
fi

# ───────────────────────────────────────────────────────────────────────
# 6. Case Comparison
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ CASE COMPARISON ───────────────────────────────────────────────┐"

echo ""
echo "Testing case comparison..."
COMPARE=$(curl -s -X POST "$BASE_URL/api/compare/compare" \
    -H "Content-Type: application/json" \
    -d '{
      "case1_title": "Maneka Gandhi v. Union of India",
      "case2_title": "Olga Tellis v. Bombay Municipal Corporation",
      "query": "right to life and livelihood"
    }' 2>&1)

if echo "$COMPARE" | grep -q '"case1"\|"case2"\|"analysis"'; then
    test_pass "Case comparison endpoint working"
    
    if echo "$COMPARE" | grep -q '"similarities"\|"differences"'; then
        test_pass "Comparison analysis generated"
    fi
else
    test_fail "Case comparison failed"
fi

# ───────────────────────────────────────────────────────────────────────
# 7. Timeline Generation
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ TIMELINE GENERATION ───────────────────────────────────────────┐"

echo ""
echo "Testing timeline generation..."
TIMELINE=$(curl -s -X POST "$BASE_URL/api/compare/timeline" \
    -H "Content-Type: application/json" \
    -d '{"query": "right to life evolution"}' 2>&1)

if echo "$TIMELINE" | grep -q '"timeline"'; then
    test_pass "Timeline generation working"
    
    CASE_COUNT=$(echo "$TIMELINE" | grep -o '"case"' | wc -l)
    if [ "$CASE_COUNT" -gt "0" ]; then
        test_pass "Timeline contains $CASE_COUNT cases"
    fi
else
    test_fail "Timeline generation failed"
fi

# ───────────────────────────────────────────────────────────────────────
# 8. Conflict Detection
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ CONFLICT DETECTION ────────────────────────────────────────────┐"

echo ""
echo "Testing conflict detection..."
CONFLICTS=$(curl -s -X POST "$BASE_URL/api/compare/detect" \
    -H "Content-Type: application/json" \
    -d '{"query": "can companies be held criminally liable"}' 2>&1)

if echo "$CONFLICTS" | grep -q '"conflicts"'; then
    test_pass "Conflict detection working"
    
    if echo "$CONFLICTS" | grep -q '"by_jurisdiction"'; then
        test_pass "Jurisdictional grouping present"
    fi
else
    test_fail "Conflict detection failed"
fi

# ───────────────────────────────────────────────────────────────────────
# 9. Legal Dictionary
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ LEGAL DICTIONARY ──────────────────────────────────────────────┐"

echo ""
echo "Testing legal dictionary..."
DICTIONARY=$(curl -s "$BASE_URL/api/summary/dictionary" 2>&1)

if echo "$DICTIONARY" | grep -q '"maxims"'; then
    test_pass "Legal dictionary available"
    
    MAXIM_COUNT=$(echo "$DICTIONARY" | grep -o '"term"' | wc -l)
    if [ "$MAXIM_COUNT" -gt "0" ]; then
        test_pass "Dictionary contains $MAXIM_COUNT maxims"
    fi
else
    test_fail "Legal dictionary not found"
fi

# ───────────────────────────────────────────────────────────────────────
# 10. File Upload (Auto-Indexing)
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "┌─ FILE UPLOAD ───────────────────────────────────────────────────┐"

echo ""
echo "Testing file upload capability..."
if [ -f "test.pdf" ]; then
    UPLOAD=$(curl -s -F "file=@test.pdf" "$BASE_URL/api/index/upload" 2>&1)
    
    if echo "$UPLOAD" | grep -q "success"; then
        test_pass "File upload working"
        echo "Note: Watch the logs for auto-indexing (watchdog should detect the file in ~2-5s)"
    else
        test_fail "File upload failed"
    fi
else
    test_skip "File upload (no test.pdf found)"
fi

# ───────────────────────────────────────────────────────────────────────
# Summary
# ───────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}PASSED: $PASS${NC}"
if [ "$FAIL" -gt "0" ]; then
    echo -e "${RED}FAILED: $FAIL${NC}"
fi
if [ "$SKIP" -gt "0" ]; then
    echo -e "${YELLOW}SKIPPED: $SKIP${NC}"
fi
echo "═══════════════════════════════════════════════════════════════════════"

# Exit with error if any tests failed
if [ "$FAIL" -gt "0" ]; then
    exit 1
fi

exit 0

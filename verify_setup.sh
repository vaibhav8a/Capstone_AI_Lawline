#!/bin/bash
# verify_setup.sh — Verify Legal RAG UI setup
# Run this to check everything is ready

set -e


RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Legal RAG System — UI Setup Verification${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"

# Check Python
echo -e "${YELLOW}[1/6]${NC} Checking Python installation..."
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    echo -e "${GREEN}  ✓ $PY_VERSION${NC}"
else
    echo -e "${RED}  ✗ Python 3 not found${NC}"
    exit 1
fi

# Check required files
echo -e "\n${YELLOW}[2/6]${NC} Checking required files..."
REQUIRED_FILES=("ui.py" "rag_api.py" "project.py" "config.py" "requirements.txt" "run_ui.sh")
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}  ✓ $file${NC}"
    else
        echo -e "${RED}  ✗ $file not found${NC}"
        exit 1
    fi
done

# Check modules
echo -e "\n${YELLOW}[3/6]${NC} Checking module files..."
MODULES=("json_loader.py" "chunk_manager.py" "embedder.py" "faiss_index.py" \
         "bm25_index.py" "knowledge_graph.py" "hybrid_retriever.py" \
         "reranker.py" "query_engine.py" "evaluator.py")
for module in "${MODULES[@]}"; do
    if [ -f "modules/$module" ]; then
        echo -e "${GREEN}  ✓ modules/$module${NC}"
    else
        echo -e "${RED}  ✗ modules/$module not found${NC}"
        exit 1
    fi
done

# Check syntax
echo -e "\n${YELLOW}[4/6]${NC} Checking Python syntax..."
if python3 -m py_compile ui.py rag_api.py > /dev/null 2>&1; then
    echo -e "${GREEN}  ✓ ui.py syntax OK${NC}"
    echo -e "${GREEN}  ✓ rag_api.py syntax OK${NC}"
else
    echo -e "${RED}  ✗ Syntax error detected${NC}"
    exit 1
fi

# Check JSON folder
echo -e "\n${YELLOW}[5/6]${NC} Checking data folder..."
if [ -d "processed_json" ] || [ -d "./processed_json" ]; then
    JSON_COUNT=$(find processed_json -name "*.json" 2>/dev/null | wc -l)
    echo -e "${GREEN}  ✓ processed_json found ($JSON_COUNT JSON files)${NC}"
else
    echo -e "${YELLOW}  ⚠ processed_json folder not found (create it first)${NC}"
fi

# Check outputs folder
echo -e "\n${YELLOW}[6/6]${NC} Checking outputs folder..."
if [ -d "outputs" ]; then
    if [ -f "outputs/vector_store.faiss" ]; then
        echo -e "${GREEN}  ✓ FAISS index exists${NC}"
    else
        echo -e "${YELLOW}  ⚠ FAISS index not built yet (run Initialize in UI)${NC}"
    fi
else
    mkdir -p outputs
    echo -e "${GREEN}  ✓ outputs folder created${NC}"
fi

# Final summary
echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Setup verification complete!${NC}\n"

echo -e "${YELLOW}Next steps:${NC}"
echo -e "  1. Install dependencies:"
echo -e "     ${BLUE}pip install -r requirements.txt${NC}"
echo -e "\n  2. Run the UI:"
echo -e "     ${BLUE}./run_ui.sh ./processed_json${NC}"
echo -e "\n  3. Open browser:"
echo -e "     ${BLUE}http://localhost:8501${NC}"

echo -e "\n${YELLOW}Documentation available:${NC}"
echo -e "  • QUICKSTART_UI.md — Quick start"
echo -e "  • UI_README.md — Full documentation"
echo -e "  • UI_IMPLEMENTATION_SUMMARY.md — Architecture details"
echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}\n"

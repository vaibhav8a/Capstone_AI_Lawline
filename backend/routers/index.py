"""
index.py
Router for triggering incremental indexing and checking system status.
"""

import os
import logging
import shutil
import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

# Add project root to sys.path to allow importing 'config'
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

from backend.services.indexing_service import IndexingService
import preprocessor

logger = logging.getLogger(__name__)

router = APIRouter()
indexer = IndexingService()

@router.post("/trigger")
async def trigger_index():
    """
    Triggers a full directory scan and delta index of new JSONs.
    """
    try:
        res = indexer.full_reindex()
        return res
    except Exception as e:
        logger.error(f"Failed to trigger index: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Request failed.")

@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Uploads a new PDF document, preprocesses it immediately, and triggers delta indexing.
    This avoids relying purely on watchdog timing and makes indexing deterministic.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
        
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        watch_folder = Path(config.WATCH_FOLDER)
        watch_folder.mkdir(parents=True, exist_ok=True)
        
        file_path = watch_folder / file.filename
        
        logger.info(f"Saving upload to: {file_path}")
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        logger.info(f"Successfully uploaded {file.filename} to {watch_folder}")

        # Deterministic ingest path: preprocess immediately and enqueue indexing.
        pp = preprocessor.Preprocessor()
        preprocess_result = preprocessor.preprocess_pdf((str(file_path), pp.output_folder, pp.ocr_lang))
        if preprocess_result.get("status") not in {"success", "skipped"}:
            raise HTTPException(
                status_code=500,
                detail=f"Preprocessing failed: {preprocess_result.get('error', 'unknown error')}"
            )

        stem = Path(file.filename).stem
        json_name = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in stem) + ".json"
        json_path = Path(pp.output_folder) / json_name
        if not json_path.exists():
            raise HTTPException(
                status_code=500,
                detail=f"Processed JSON not found after preprocessing: {json_path}"
            )

        job_ref = indexer.enqueue_processed_json(str(json_path))
        return {
            "status": "success",
            "filename": file.filename,
            "path": str(file_path),
            "processed_json": str(json_path),
            "index_job": job_ref,
            "preprocess_status": preprocess_result.get("status"),
        }
    except Exception as e:
        logger.error(f"Failed to upload file {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/status/{job_id}")
async def get_status(job_id: str):
    """
    Checks the status of an RQ indexing job.
    """
    res = indexer.get_job_status(job_id)
    return res

@router.get("/health")
async def health_check():
    """
    System health check endpoint - verifies all components are initialized.
    """
    from backend.services.rag_service import rag_service
    import os
    
    try:
        health = {
            "status": "healthy",
            "rag_service_initialized": rag_service.initialized,
            "watch_folder": str(config.WATCH_FOLDER),
            "processed_json_folder": str(config.PROCESSED_JSON_FOLDER),
            "chroma_db_path": str(config.CHROMA_PERSIST_PATH),
            "auth_bypass": os.getenv("DISABLE_AUTH", "true").lower() == "true",
            "components": {
                "chroma": rag_service.chroma is not None,
                "bm25": rag_service.bm25 is not None,
                "kg": rag_service.kg is not None,
                "hybrid_retriever": rag_service.hybrid_retriever is not None,
                "query_engine": rag_service.query_engine is not None,
                "case_summarizer": rag_service.case_summarizer is not None,
            }
        }
        
        # Check file counts
        import glob
        json_files = glob.glob(str(config.PROCESSED_JSON_FOLDER / "*.json"))
        health["indexed_documents"] = len(json_files)
        
        return health
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {"status": "unhealthy", "error": str(e)}

"""
indexing_service.py
Service layer for orchestrating system-wide or delta indexing.
Connects with Redis/RQ to offload jobs.
"""

import logging
import os
import glob
import json
from pathlib import Path
from redis import Redis
from rq import Queue

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

class IndexingService:
    def __init__(self):
        self.index_manifest_path = config.OUTPUT_DIR / "index_manifest.json"
        self._inflight_paths = set()
        try:
            self.redis_conn = Redis.from_url(config.REDIS_URL)
            self.redis_conn.ping()
            self.queue = Queue("legal-rag", connection=self.redis_conn)
            self.redis_available = True
        except Exception as e:
            logger.warning(f"[IndexingService] Redis unavailable, operations will be synchronous fallback: {e}")
            self.redis_available = False

    def _load_index_manifest(self):
        if self.index_manifest_path.exists():
            try:
                with open(self.index_manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_index_manifest(self, manifest):
        self.index_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    def should_index_json(self, json_path: str) -> bool:
        """
        Index only when processed JSON content (document hash) has changed.
        """
        if not os.path.exists(json_path):
            return False
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            document_hash = payload.get("document_hash")
            if not document_hash:
                return True
            manifest = self._load_index_manifest()
            key = os.path.basename(json_path)
            return manifest.get(key) != document_hash
        except Exception:
            return True

    def mark_indexed_json(self, json_path: str):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            document_hash = payload.get("document_hash")
            if not document_hash:
                return
            manifest = self._load_index_manifest()
            manifest[os.path.basename(json_path)] = document_hash
            self._save_index_manifest(manifest)
        except Exception as e:
            logger.warning(f"[IndexingService] Failed to update index manifest for {json_path}: {e}")

    def enqueue_processed_json(self, json_path: str):
        """Asynchronously triggers delta indexing for a processed JSON."""
        if json_path in self._inflight_paths:
            return {"status": "already_queued", "json_path": json_path}

        if not self.should_index_json(json_path):
            logger.info(f"[IndexingService] Skipping unchanged indexed JSON: {json_path}")
            return {"status": "skipped", "json_path": json_path}

        self._inflight_paths.add(json_path)
        if self.redis_available:
            from backend.workers.delta_worker import process_delta_index
            job = self.queue.enqueue(process_delta_index, json_path)
            logger.info(f"[IndexingService] Enqueued {json_path} as job {job.id}")
            self._inflight_paths.discard(json_path)
            return job.id
        else:
            # Synchronous fallback
            from backend.workers.delta_worker import process_delta_index
            result = process_delta_index(json_path)
            if isinstance(result, dict) and result.get("status") == "success":
                self.mark_indexed_json(json_path)
            self._inflight_paths.discard(json_path)
            return result

    def full_reindex(self):
        """Triggers indexing for all files in processed_json_folder."""
        json_folder = config.PROCESSED_JSON_FOLDER
        if not os.path.exists(json_folder):
            logger.error("[IndexingService] No processed JSON directory found.")
            return {"status": "error", "message": "No processed JSON directory"}
            
        files = glob.glob(str(Path(json_folder) / "*.json"))
        self._prune_manifest(files)
        logger.info(f"[IndexingService] Initiating full reindex of {len(files)} files...")
        
        job_ids = []
        for f in files:
            j_id = self.enqueue_processed_json(f)
            job_ids.append(j_id)
            
        return {"status": "queued", "job_count": len(job_ids), "jobs": job_ids}

    def _prune_manifest(self, existing_files):
        manifest = self._load_index_manifest()
        keep = {os.path.basename(f) for f in existing_files}
        stale = [k for k in manifest.keys() if k not in keep]
        for key in stale:
            manifest.pop(key, None)
        if stale:
            self._save_index_manifest(manifest)
        
    def get_job_status(self, job_id: str):
        if not self.redis_available:
            return {"status": "unknown (no redis)"}
            
        job = self.queue.fetch_job(job_id)
        if not job:
            return {"status": "not_found"}
            
        return {
            "status": job.get_status(),
            "result": job.result,
            "error": job.exc_info
        }

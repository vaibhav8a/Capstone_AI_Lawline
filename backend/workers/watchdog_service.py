"""
watchdog_service.py
Monitors the PDF input folder. When a new PDF is detected, it runs the preprocessor,
then queues a delta_index job in RQ.
"""

import importlib.util
import os
import sys
import time
import logging
import re
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Project root is three levels up from this file
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def _load_root_module(name: str):
    """Dynamically load a module from the project root by file path."""
    spec = importlib.util.spec_from_file_location(name, _PROJECT_ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

preprocessor = _load_root_module("preprocessor")
config       = _load_root_module("config")

# Import IndexingService after sys.path is set
from backend.services.indexing_service import IndexingService

logger = logging.getLogger(__name__)

class PDFHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.indexer = IndexingService()
        self._recent = {}

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.pdf'):
            self.process_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith('.pdf'):
            self.process_file(event.dest_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.pdf'):
            self.process_file(event.src_path)

    def process_file(self, file_path):
        now = time.time()
        last_seen = self._recent.get(file_path, 0)
        if now - last_seen < 2.0:
            return
        self._recent[file_path] = now
        logger.info(f"[Watchdog] Processing file: {file_path}")
        try:
            # Wait for file to stabilize
            last_size = -1
            while True:
                current_size = os.path.getsize(file_path)
                if current_size == last_size:
                    break
                last_size = current_size
                time.sleep(1)

            p = preprocessor.Preprocessor()
            args = (file_path, p.output_folder, p.ocr_lang)
            res = preprocessor.preprocess_pdf(args)

            if res["status"] in ["success", "skipped"]:
                # Trigger delta indexing
                base = os.path.basename(file_path)
                json_name = re.sub(r"[^a-zA-Z0-9_]", "_", os.path.splitext(base)[0]) + ".json"
                json_path = os.path.join(p.output_folder, json_name)
                
                if os.path.exists(json_path):
                    logger.info(f"[Watchdog] Triggering indexing for {json_path}")
                    self.indexer.enqueue_processed_json(json_path)
                else:
                    logger.warning(f"[Watchdog] Processed JSON not found: {json_path}")
            else:
                logger.error(f"[Watchdog] Preprocessing failed for {file_path}: {res.get('error')}")

        except Exception as e:
            logger.error(f"[Watchdog] Error processing {file_path}: {e}", exc_info=True)

class WatchdogService:
    def __init__(self):
        self.watch_dir = str(config.WATCH_FOLDER)
        self.observer = Observer()
        
    def start(self):
        os.makedirs(self.watch_dir, exist_ok=True)
        event_handler = PDFHandler()
        self.observer.schedule(event_handler, self.watch_dir, recursive=False)
        self.observer.start()
        logger.info(f"[WatchdogService] Started watching {self.watch_dir}")
        
    def stop(self):
        self.observer.stop()
        self.observer.join()
        logger.info("[WatchdogService] Stopped watching.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    wd = WatchdogService()
    wd.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        wd.stop()

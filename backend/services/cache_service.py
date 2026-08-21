"""
cache_service.py
Provides a robust caching layer using DiskCache as primary local cache,
with an optional Redis network cache structure if needed.
"""

import hashlib
import json
import logging
from diskcache import Cache
from prometheus_client import Counter

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)
CACHE_HITS = Counter("cache_hits_total", "Cache hits", ["prefix"])
CACHE_MISSES = Counter("cache_misses_total", "Cache misses", ["prefix"])

class CacheService:
    def __init__(self):
        self.enabled = config.CACHE_ENABLED
        self.ttl = config.CACHE_TTL_HOURS * 3600
        
        # Local DiskCache
        self.cache = Cache(str(config.CACHE_DIR), statistics=True)
        logger.info(f"[CacheService] Initialized DiskCache at {config.CACHE_DIR}")

    def _generate_key(self, prefix: str, **kwargs) -> str:
        """Deterministically generates a cache key from arguments."""
        # Sort keys to ensure dictionary order doesn't affect hash
        raw_string = json.dumps(kwargs, sort_keys=True)
        key_hash = hashlib.md5(raw_string.encode('utf-8')).hexdigest()
        return f"{prefix}:{key_hash}"

    def get(self, prefix: str, **kwargs):
        """Retrieves value from cache."""
        if not self.enabled: return None
        
        key = self._generate_key(prefix, **kwargs)
        val = self.cache.get(key)
        
        if val:
            logger.debug(f"[CacheService] HIT for {key}")
            CACHE_HITS.labels(prefix=prefix).inc()
        else:
            logger.debug(f"[CacheService] MISS for {key}")
            CACHE_MISSES.labels(prefix=prefix).inc()
            
        return val

    def set(self, prefix: str, value: any, **kwargs):
        """Sets a value in the cache with the configured TTL."""
        if not self.enabled: return
        
        key = self._generate_key(prefix, **kwargs)
        self.cache.set(key, value, expire=self.ttl)
        logger.debug(f"[CacheService] SET for {key}")

    def clear(self):
        """Clears the entire cache."""
        self.cache.clear()
        logger.info("[CacheService] Cache cleared.")

    def get_stats(self):
        hits, misses = self.cache.stats()
        count = len(self.cache)
        return {
            "hits": hits,
            "misses": misses,
            "item_count": count
        }

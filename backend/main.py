import logging
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.services.rag_service import rag_service
from backend.workers.watchdog_service import WatchdogService

# Observability
from prometheus_client import make_asgi_app
from prometheus_client import Counter, Histogram
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except Exception:
    OTLPSpanExporter = None

# Import routers
from backend.routers import query, index, graph, compare, summary, critique, statute
from backend.routers.auth import router as auth_router
from backend.routers.export import router as export_router
from backend.routers.eval_router import router as eval_router

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _configure_tracing():
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint or OTLPSpanExporter is None:
        logger.info("OTLP exporter not configured; using in-process instrumentation only.")
        return
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry exporter configured: %s", endpoint)

limiter = Limiter(key_func=get_remote_address)
wd_service = WatchdogService()
REQUEST_LATENCY = Histogram("api_request_latency_seconds", "API latency", ["path", "method"])
REQUEST_COUNT = Counter("api_request_total", "Total API requests", ["path", "method", "status"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB, Redis, load models
    logger.info("Initializing LawLine AI Backend...")
    _configure_tracing()
    # Initialize singleton service
    await rag_service.initialize()
    if not rag_service.initialized:
        logger.error("Failed to initialize RAG system.")
        
    # Start background file watcher
    import threading
    t = threading.Thread(target=wd_service.start, daemon=True)
    t.start()
    
    yield
    
    # Shutdown: Clean up resources
    logger.info("Shutting down LawLine AI Backend...")
    wd_service.stop()

app = FastAPI(
    title="Legal RAG API",
    version="2.0.0",
    description="Production-grade Legal QA & Shepardization pipeline",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://0.0.0.0:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    import time
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    path = request.url.path
    method = request.method
    REQUEST_LATENCY.labels(path=path, method=method).observe(elapsed)
    REQUEST_COUNT.labels(path=path, method=method, status=str(response.status_code)).inc()
    return response

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "legal-rag", "initialized": rag_service.initialized}

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # The exception text is logged server-side with a correlation id, but never
    # returned to the client: internal messages routinely carry filesystem paths,
    # collection names, model identifiers and occasionally fragments of config.
    # The client gets the id so a user can quote it when reporting a problem.
    error_id = uuid.uuid4().hex[:12]
    logger.error("Unhandled error [%s] on %s: %s", error_id, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred.", "error_id": error_id},
    )

# Register routers
app.include_router(auth_router,       prefix="/auth",         tags=["Auth"])
app.include_router(statute.router,    prefix="/api/statute",  tags=["Statute QA"])
app.include_router(query.router,      prefix="/api/query",    tags=["Query"])
app.include_router(index.router,      prefix="/api/index",    tags=["Index"])
app.include_router(graph.router,      prefix="/api/graph",    tags=["Graph"])
app.include_router(compare.router,    prefix="/api/compare",  tags=["Compare"])
app.include_router(summary.router,    prefix="/api/summary",  tags=["Summary"])
app.include_router(critique.router,   prefix="/api/critique", tags=["Critique"])
app.include_router(export_router,     prefix="/api/export",   tags=["Export"])
app.include_router(eval_router,       prefix="/api/eval",     tags=["Evaluation"])

# Metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

if __name__ == "__main__":
    import uvicorn
    # In development with --reload, ignore folders that change frequently to avoid infinite loops/interrupts
    uvicorn.run(
        "backend.main:app", 
        host="0.0.0.0", 
        port=8001, 
        reload=True,
        reload_excludes=["Dataset/*", "processed_json/*", "outputs/*", "*.log", "preprocessing.log"]
    )


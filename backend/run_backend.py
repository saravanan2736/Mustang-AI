"""
Mustang AI Backend Launcher
Runs FastAPI backend on 127.0.0.1:18000 with lifespan management.
"""

import sys
import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"=================================================")
    print(f"   MUSTANG AI — Local Inference API Backend     ")
    print(f"   Binding: http://{settings.HOST}:{settings.PORT}")
    print(f"   Runtime Dir: {settings.RUNTIME_DIR}")
    print(f"   Models Dir: {settings.MODELS_DIR}")
    print(f"=================================================")
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="info",
        reload=False,
    )


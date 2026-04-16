import os
from pathlib import Path

import uvicorn


def main() -> None:
    backend_root = Path(__file__).resolve().parent
    project_root = backend_root.parent
    os.chdir(backend_root)
    os.environ.setdefault("APP_ROOT", os.fspath(backend_root))
    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()

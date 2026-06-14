"""Entry point: python -m muffin.manager"""

import uvicorn

from .. import config


def main() -> None:
    print(f"[muffin] manager starting on http://{config.MANAGER_HOST}:{config.MANAGER_PORT}")
    print(f"[muffin] database: {config.DB_PATH}")
    uvicorn.run(
        "muffin.manager.app:app",
        host=config.MANAGER_HOST,
        port=config.MANAGER_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()

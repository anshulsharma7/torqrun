"""Entry point: ``python -m torqrun_api`` or the ``torqrun-api`` script."""

import uvicorn

from torqrun_api.main import create_app
from torqrun_api.settings import Settings


def main() -> None:
    settings = Settings()  # values come from the environment
    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,  # create_app configures logging
            proxy_headers=True,
            timeout_graceful_shutdown=20,
        )
    )
    # Uvicorn stops accepting connections on SIGTERM, then waits for in-flight requests. Let
    # long-polls and live streams see that and finish at once instead of holding shutdown (and
    # every agent's heartbeat) hostage for their full wait time.
    app.state.is_shutting_down = lambda: server.should_exit
    server.run()


if __name__ == "__main__":
    main()

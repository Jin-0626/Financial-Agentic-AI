"""Stable ASGI entry point; feature imports do not initialize the server."""


def __getattr__(name):
    if name == "app":
        from app.bootstrap.application import app

        return app
    raise AttributeError(name)

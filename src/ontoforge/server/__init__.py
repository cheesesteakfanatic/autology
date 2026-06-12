"""OntoForge web application (``ontoforge serve``): REST API + SPA over a
project directory — the product surface for analysts and reviewers.

Public surface:

    create_app(project) -> fastapi.FastAPI
    run_server(project, host=..., port=...)
"""

from .app import create_app, run_server

__all__ = ["create_app", "run_server"]

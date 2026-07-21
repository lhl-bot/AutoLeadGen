"""AutoLeadGen Product V2 isolated domain package.

The package does not import the FastAPI router eagerly.  Migrations and worker
processes can therefore import the ORM without pulling in the HTTP application.
"""

__all__: list[str] = []

# =============================================================================
# Storage backend factory
# =============================================================================
# Set the STORAGE_BACKEND environment variable to choose a backend:
#
#   STORAGE_BACKEND=local       Local JSON files — no cloud account needed
#                               (default when the variable is unset)
#   STORAGE_BACKEND=firestore   Google Cloud Firestore
#   STORAGE_BACKEND=dynamodb    AWS DynamoDB
#   STORAGE_BACKEND=cosmosdb    Azure Cosmos DB (NoSQL / Core API)
#   STORAGE_BACKEND=mongodb     MongoDB Atlas or self-hosted MongoDB
#
# See each backend module for its required environment variables and
# install instructions.
# =============================================================================

import os
from functools import lru_cache

from src.backend.base_storage import StorageBackend

_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """
    Return a singleton StorageBackend instance for the configured provider.

    The instance is cached after the first call so the SDK client is
    initialised only once per process.
    """
    if _BACKEND == "firestore":
        from src.backend.firestore_storage import FirestoreStorage
        return FirestoreStorage()

    if _BACKEND == "dynamodb":
        from src.backend.dynamodb_storage import DynamoDBStorage
        return DynamoDBStorage()

    if _BACKEND == "cosmosdb":
        from src.backend.cosmosdb_storage import CosmosDBStorage
        return CosmosDBStorage()

    if _BACKEND == "mongodb":
        from src.backend.mongodb_storage import MongoDBStorage
        return MongoDBStorage()

    if _BACKEND == "local":
        from src.backend.local_storage import LocalStorage
        return LocalStorage()

    raise ValueError(
        f"Unknown STORAGE_BACKEND='{_BACKEND}'. "
        "Supported values: local | firestore | dynamodb | cosmosdb | mongodb"
    )

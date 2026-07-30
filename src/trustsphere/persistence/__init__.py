from trustsphere.persistence.base import Repository
from trustsphere.persistence.local import LocalSQLiteRepository

__all__ = ["Repository", "LocalSQLiteRepository", "get_repository"]


def get_repository() -> Repository:
    """Factory selecting the backend from Settings.data_backend.

    Kept import-lazy for the HANA branch so `hdbcli` (and a live network
    path) is never required just to run the local/test path.
    """
    from trustsphere.config import get_settings

    settings = get_settings()
    if settings.data_backend == "hana":
        from trustsphere.persistence.hana import HanaRepository

        return HanaRepository(settings)
    return LocalSQLiteRepository(settings.local_db_path)

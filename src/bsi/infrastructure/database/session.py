"""
SQLAlchemy engine and session lifecycle for BSI persistence.

This module provides:

- PostgreSQL engine construction
- SQLAlchemy session-factory construction
- Transaction commit and rollback handling
- Reliable session cleanup

It does not:

- Read global application settings directly
- Define ORM database tables
- Contain repository queries
- Contain accounting or rule-engine logic

Application startup code will inject validated database configuration
into the functions defined here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

POSTGRESQL_DRIVER_NAME: Final = "postgresql+psycopg"
"""Required synchronous PostgreSQL driver for the BSI backend."""


class DatabaseConfigurationError(ValueError):
    """Raised when database infrastructure configuration is invalid."""


type DatabaseSessionFactory = sessionmaker[Session]
"""Typed factory used to create SQLAlchemy database sessions."""


def create_database_engine(
    *,
    database_url: str,
    echo: bool = False,
) -> Engine:
    """
    Create the synchronous SQLAlchemy PostgreSQL engine.

    Parameters
    ----------
    database_url:
        SQLAlchemy connection URL using the ``postgresql+psycopg``
        driver.

    echo:
        Whether SQLAlchemy should print generated SQL statements.

    Returns
    -------
    Engine
        Configured SQLAlchemy engine.

    Raises
    ------
    DatabaseConfigurationError
        If the URL or echo setting is invalid.

    Notes
    -----
    Creating an engine does not immediately connect to PostgreSQL.
    SQLAlchemy opens a database connection when the application first
    performs database work.
    """

    validated_url = _validate_database_url(database_url)

    if not isinstance(echo, bool):
        raise DatabaseConfigurationError("echo must be a boolean.")

    return create_engine(
        validated_url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_session_factory(
    *,
    engine: Engine,
) -> DatabaseSessionFactory:
    """
    Create the authoritative BSI SQLAlchemy session factory.

    Parameters
    ----------
    engine:
        Configured synchronous SQLAlchemy engine.

    Returns
    -------
    DatabaseSessionFactory
        Callable factory that creates SQLAlchemy sessions.

    Raises
    ------
    DatabaseConfigurationError
        If the supplied value is not a SQLAlchemy engine.

    Notes
    -----
    ``autoflush=False`` keeps database writes explicit. Repository code
    can call ``flush()`` when it needs generated identifiers or database
    constraint validation before committing.

    ``expire_on_commit=False`` keeps loaded attributes available after a
    successful commit. This prevents unnecessary reloads when returning
    persistence results through application services.
    """

    if not isinstance(engine, Engine):
        raise DatabaseConfigurationError("engine must be a SQLAlchemy Engine.")

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(
    *,
    session_factory: DatabaseSessionFactory,
) -> Iterator[Session]:
    """
    Provide one transactional SQLAlchemy session.

    Parameters
    ----------
    session_factory:
        Factory used to create the database session.

    Yields
    ------
    Session
        Active SQLAlchemy session.

    Raises
    ------
    Exception
        Any application, repository, SQLAlchemy, or database exception
        is rolled back and then re-raised unchanged.

    Transaction behavior
    --------------------
    Successful execution:
        yield session → commit → close

    Failed execution:
        yield session → rollback → re-raise → close
    """

    if not isinstance(session_factory, sessionmaker):
        raise DatabaseConfigurationError(
            "session_factory must be a SQLAlchemy sessionmaker."
        )

    session = session_factory()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _validate_database_url(
    database_url: object,
) -> URL:
    """
    Validate and parse one SQLAlchemy PostgreSQL connection URL.

    The original URL is never included in error messages because it may
    contain credentials.
    """

    if not isinstance(database_url, str):
        raise DatabaseConfigurationError("database_url must be a string.")

    cleaned_database_url = database_url.strip()

    if not cleaned_database_url:
        raise DatabaseConfigurationError("database_url cannot be empty.")

    try:
        parsed_url = make_url(cleaned_database_url)
    except ArgumentError as exc:
        raise DatabaseConfigurationError(
            "database_url must be a valid SQLAlchemy URL."
        ) from exc

    if parsed_url.drivername != POSTGRESQL_DRIVER_NAME:
        raise DatabaseConfigurationError(
            "database_url must use the postgresql+psycopg driver."
        )

    if not parsed_url.database:
        raise DatabaseConfigurationError("database_url must include a database name.")

    return parsed_url


__all__ = [
    "POSTGRESQL_DRIVER_NAME",
    "DatabaseConfigurationError",
    "DatabaseSessionFactory",
    "create_database_engine",
    "create_session_factory",
    "session_scope",
]

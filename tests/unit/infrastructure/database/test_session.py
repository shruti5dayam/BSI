"""
Unit tests for the BSI SQLAlchemy engine and session lifecycle.

These tests verify:

- PostgreSQL engine construction
- Database URL validation
- SQL echo validation
- Session-factory configuration
- Successful commit and cleanup
- Failure rollback and cleanup
- Runtime dependency validation

The tests do not require a running PostgreSQL server because SQLAlchemy
creates database engines lazily.
"""

from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bsi.infrastructure.database.session import (
    POSTGRESQL_DRIVER_NAME,
    DatabaseConfigurationError,
    create_database_engine,
    create_session_factory,
    session_scope,
)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should provide correctly typed configuration and
    SQLAlchemy infrastructure objects. These tests verify protection
    against invalid runtime inputs.
    """

    return cast(Any, value)


@pytest.mark.unit
def test_create_database_engine_returns_postgresql_engine() -> None:
    """A valid URL should create a synchronous PostgreSQL engine."""

    engine = create_database_engine(
        database_url=("postgresql+psycopg://bsi_user:secret@localhost:5432/bsi"),
        echo=False,
    )

    try:
        assert isinstance(engine, Engine)
        assert engine.url.drivername == POSTGRESQL_DRIVER_NAME
        assert engine.url.database == "bsi"
        assert engine.echo is False
    finally:
        engine.dispose()


@pytest.mark.unit
def test_create_database_engine_accepts_echo_enabled() -> None:
    """SQL output may be enabled explicitly for local diagnostics."""

    engine = create_database_engine(
        database_url=("postgresql+psycopg://bsi_user:secret@localhost:5432/bsi"),
        echo=True,
    )

    try:
        assert engine.echo is True
    finally:
        engine.dispose()


@pytest.mark.unit
def test_create_database_engine_rejects_non_string_url() -> None:
    """Database URLs must be supplied as strings."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="database_url must be a string",
    ):
        create_database_engine(
            database_url=_invalid(123),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "   ",
    ],
)
def test_create_database_engine_rejects_blank_url(
    database_url: str,
) -> None:
    """Blank database configuration must fail before engine creation."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="database_url cannot be empty",
    ):
        create_database_engine(
            database_url=database_url,
        )


@pytest.mark.unit
def test_create_database_engine_rejects_wrong_driver() -> None:
    """BSI persistence must use the configured Psycopg driver."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="must use the",
    ):
        create_database_engine(
            database_url="sqlite+pysqlite:///:memory:",
        )


@pytest.mark.unit
def test_create_database_engine_rejects_missing_database_name() -> None:
    """A PostgreSQL URL must identify the target database."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="must include a database name",
    ):
        create_database_engine(
            database_url=("postgresql+psycopg://bsi_user:secret@localhost:5432"),
        )


@pytest.mark.unit
def test_create_database_engine_rejects_non_boolean_echo() -> None:
    """SQL echo must use an explicit boolean value."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="echo must be a boolean",
    ):
        create_database_engine(
            database_url=("postgresql+psycopg://bsi_user:secret@localhost:5432/bsi"),
            echo=_invalid("false"),
        )


@pytest.mark.unit
def test_create_session_factory_configures_sessions() -> None:
    """
    The session factory should disable implicit flushing and expiration.

    SQLite is used only as an isolated unit-test engine. Production BSI
    configuration remains restricted to PostgreSQL.
    """

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
    )

    try:
        session_factory = create_session_factory(
            engine=engine,
        )
        session = session_factory()

        try:
            assert isinstance(
                session_factory,
                sessionmaker,
            )
            assert isinstance(session, Session)
            assert session.get_bind() is engine
            assert session.autoflush is False
            assert session.expire_on_commit is False
        finally:
            session.close()
    finally:
        engine.dispose()


@pytest.mark.unit
def test_create_session_factory_rejects_invalid_engine() -> None:
    """The session factory requires a real SQLAlchemy engine."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="engine must be a SQLAlchemy Engine",
    ):
        create_session_factory(
            engine=_invalid("not-an-engine"),
        )


@pytest.mark.unit
def test_session_scope_commits_and_closes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful work must commit before the session is closed."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
    )
    session_factory = create_session_factory(
        engine=engine,
    )
    events: list[str] = []

    def record_commit(session: Session) -> None:
        """Record the commit operation without database interaction."""

        del session
        events.append("commit")

    def record_close(session: Session) -> None:
        """Record session cleanup."""

        del session
        events.append("close")

    monkeypatch.setattr(
        Session,
        "commit",
        record_commit,
    )
    monkeypatch.setattr(
        Session,
        "close",
        record_close,
    )

    try:
        with session_scope(
            session_factory=session_factory,
        ) as session:
            assert isinstance(session, Session)
            events.append("body")

        assert events == [
            "body",
            "commit",
            "close",
        ]
    finally:
        engine.dispose()


@pytest.mark.unit
def test_session_scope_rolls_back_and_closes_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed work must roll back, preserve the error, and close."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
    )

    session_factory = create_session_factory(
        engine=engine,
    )
    events: list[str] = []

    def record_commit(session: Session) -> None:
        """Record any unexpected commit attempt."""

        del session
        events.append("commit")

    def record_rollback(session: Session) -> None:
        """Record transaction rollback."""

        del session
        events.append("rollback")

    def record_close(session: Session) -> None:
        """Record session cleanup."""

        del session
        events.append("close")

    monkeypatch.setattr(
        Session,
        "commit",
        record_commit,
    )
    monkeypatch.setattr(
        Session,
        "rollback",
        record_rollback,
    )
    monkeypatch.setattr(
        Session,
        "close",
        record_close,
    )

    try:
        with (
            pytest.raises(
                RuntimeError,
                match="Repository operation failed",
            ),
            session_scope(
                session_factory=session_factory,
            ),
        ):
            events.append("body")
            raise RuntimeError("Repository operation failed.")

        assert events == [
            "body",
            "rollback",
            "close",
        ]
    finally:
        engine.dispose()


@pytest.mark.unit
def test_session_scope_rejects_invalid_session_factory() -> None:
    """The transaction boundary requires a SQLAlchemy sessionmaker."""

    with (
        pytest.raises(
            DatabaseConfigurationError,
            match="session_factory must be a SQLAlchemy sessionmaker",
        ),
        session_scope(
            session_factory=_invalid("not-a-session-factory"),
        ),
    ):
        pytest.fail("An invalid session factory must not yield a session.")

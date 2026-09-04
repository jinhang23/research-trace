"""SQLAlchemy-managed SQLite connections and Alembic schema upgrades."""
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL


def open_database(path: Path):
    engine = create_engine(URL.create('sqlite', database=str(path)),
                           connect_args={'check_same_thread': False, 'isolation_level': None})

    @event.listens_for(engine, 'connect')
    def configure(connection, _record):
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA busy_timeout=30000')

    try:
        with engine.connect() as connection:
            # Explicit transaction keeps migration adoption atomic with SQLite
            # autocommit mode; the Store retains its domain IMMEDIATE transactions.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            config = Config()
            config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.commit()
    except Exception:
        engine.dispose()
        raise
    return engine

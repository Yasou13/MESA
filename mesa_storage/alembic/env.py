from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url

from mesa_storage.schema_contract import preflight_schema, validate_postflight

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _migration_url() -> str:
    """Use the synchronous SQLite driver for Alembic DDL execution.

    MESA's application runtime intentionally uses ``aiosqlite``.  Alembic's
    migration operations are synchronous, however, and running them through
    the async adapter can leave the CLI waiting on its event loop.  This
    conversion is scoped to the migration process only.
    """
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url is None:
        raise RuntimeError("Alembic sqlalchemy.url is required for migration")
    url = make_url(configured_url)
    if url.drivername == "sqlite+aiosqlite":
        url = url.set(drivername="sqlite+pysqlite")
    return str(url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    x_arguments = context.get_x_argument(as_dictionary=True)
    if x_arguments.get("mesa_legacy") == "adopt":
        raise RuntimeError(
            "mesa_legacy=adopt requires an online SQLite connection; offline SQL "
            "generation cannot inspect or adopt a legacy database."
        )
    url = _migration_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    preflight_schema(connection, config)
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
    validate_postflight(
        connection,
        config,
        require_head=bool(config.attributes.get("mesa_require_head_postflight")),
    )


def run_migrations_online() -> None:
    """Run migrations through Alembic's synchronous DDL connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _migration_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            do_run_migrations(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

import os

from alembic import context
from sqlalchemy import create_engine, pool

from trilha.db import Base

config = context.config
url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        schema = os.environ.get("TRILHA_TEST_SCHEMA")
        if schema:
            if not schema.startswith("test_trilha_") or not schema.replace("_", "").isalnum():
                raise RuntimeError("invalid_test_schema")
            connection.exec_driver_sql('SET search_path TO "' + schema + '"')
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

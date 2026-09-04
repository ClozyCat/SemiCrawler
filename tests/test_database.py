from sqlalchemy import create_engine, inspect, text

from app import database


def test_legacy_database_migration_adds_baidu_api_key(tmp_path, monkeypatch):
    legacy_engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE model_settings (
                    id INTEGER PRIMARY KEY,
                    base_url VARCHAR(500),
                    model_name VARCHAR(200),
                    api_key TEXT,
                    tavily_api_key TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE scheduled_tasks (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(120),
                    frequency VARCHAR(20),
                    hour INTEGER,
                    start_date DATE,
                    source_ids_json TEXT,
                    enabled BOOLEAN
                )
                """
            )
        )

    monkeypatch.setattr(database, "engine", legacy_engine)
    database.migrate_legacy_database()

    columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("model_settings")
    }
    assert "baidu_api_key" in columns
    schedule_columns = {
        column["name"]
        for column in inspect(legacy_engine).get_columns("scheduled_tasks")
    }
    assert {"keyword_filter_enabled", "auto_structure_enabled"} <= schedule_columns

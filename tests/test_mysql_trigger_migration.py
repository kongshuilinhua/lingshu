"""MySQL constraint setup resumes after an implicitly committed partial DDL run."""

from contextlib import contextmanager

import pytest

from core.db import session as db_session


def test_trigger_bundle_recovers_after_partial_failure(monkeypatch):
    state = {"columns": set(), "indexes": set(), "triggers": set(), "fail_once": True, "steps": []}

    class Inspector:
        def get_columns(self, table):
            return [{"name": name} for name in state["columns"]]

        def get_indexes(self, table):
            return [{"name": name} for name in state["indexes"]]

    class Result:
        def scalars(self):
            return iter(state["triggers"])

    class Connection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if sql.startswith("SELECT TRIGGER_NAME"):
                return Result()
            state["steps"].append(sql)
            if sql.startswith("ALTER TABLE"):
                state["columns"].add("unique_column")
            elif sql.startswith("CREATE UNIQUE INDEX"):
                state["indexes"].add("unique_index")
            elif sql.startswith("CREATE TRIGGER"):
                if state["fail_once"]:
                    state["fail_once"] = False
                    raise RuntimeError("trigger permission unavailable")
                state["triggers"].add(sql.split()[2])

    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()

        @contextmanager
        def connect(self):
            yield Connection()

    monkeypatch.setattr(db_session, "engine", Engine())
    monkeypatch.setattr(db_session, "inspect", lambda engine: Inspector())
    args = (
        "example", "unique_column", "ALTER TABLE example ADD COLUMN unique_column VARCHAR(64)",
        "UPDATE example SET unique_column = 'filled'",
        "unique_index", "CREATE UNIQUE INDEX unique_index ON example (unique_column)",
        {"trigger_insert": "CREATE TRIGGER trigger_insert BEFORE INSERT ON example SET @x = 1"},
    )
    with pytest.raises(RuntimeError, match="permission"):
        db_session._ensure_mysql_trigger_bundle(*args)
    db_session._ensure_mysql_trigger_bundle(*args)
    assert state["triggers"] == {"trigger_insert"}
    assert sum(step.startswith("ALTER TABLE") for step in state["steps"]) == 1
    assert sum(step.startswith("CREATE UNIQUE INDEX") for step in state["steps"]) == 1
    assert sum(step.startswith("UPDATE example") for step in state["steps"]) == 2

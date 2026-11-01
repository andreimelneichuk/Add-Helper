"""Каждый процесс сам находит таблицы, на которые ссылаются его модели.

Тесты загружают все модели сразу, поэтому внешний ключ на таблицу из другого модуля
проходит в тестах и падает в воркере при первой записи.
"""

import subprocess
import sys

import pytest

CHECK = """
import importlib, sys
importlib.import_module(sys.argv[1])
from adhelper.db import Base
for table in Base.metadata.tables.values():
    for key in table.foreign_keys:
        key.column
"""


@pytest.mark.parametrize("entrypoint", ["adhelper.worker.app", "adhelper.api.app"])
def test_foreign_keys_resolve_in_fresh_process(entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK, entrypoint], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr.strip().splitlines()[-1]

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Dict
from unittest.mock import patch


_SCRIPT = Path(__file__).resolve().parent.parent / "slurm-quota"


def load_module():
    mod = sys.modules.get("slurm_quota")
    if mod is not None:
        return mod
    loader = SourceFileLoader("slurm_quota", str(_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Unable to create module spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["slurm_quota"] = mod
    loader.exec_module(mod)
    return mod


class SlurmQuotaTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sq = load_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "slurm-quota.db")
        self._patch_db = patch.object(self.sq, "DB_PATH", self.db_path)
        self._patch_db.start()
        self.addCleanup(self._patch_db.stop)
        self.addCleanup(self._tmp.cleanup)

    def init_db(self):
        self.sq.init_database()

    def db_connect(self):
        return sqlite3.connect(self.db_path)

    def env(self, updates: Dict[str, str]):
        p = patch.dict(os.environ, updates, clear=False)
        p.start()
        self.addCleanup(p.stop)
        return p

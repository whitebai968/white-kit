#!/usr/bin/env python3
"""Run isolated tests without installing a service or reading personal sessions."""
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parent
sys.path[:0] = [str(root / 'src'), str(root)]
sys.dont_write_bytecode = True
suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)

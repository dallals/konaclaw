#!/usr/bin/env python3
"""Stream workspace/finances.md to stdout.

A thin wrapper so Kona can read the file through `skill_run_script`
(which only runs scripts, not arbitrary `cat`). realpath lets the
script work when invoked through a symlink from the skill's scripts/
directory.
"""
import os
import sys

here = os.path.dirname(os.path.realpath(__file__))
path = os.path.join(here, "finances.md")
with open(path) as f:
    sys.stdout.write(f.read())

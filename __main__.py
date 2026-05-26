#!/usr/bin/env python3
"""quale — grammar-free structural codebase analyzer.

Usage:
  quale analyze [path]           analyze codebase structure
  quale diff <ref_a> <ref_b>     compare two git refs
  quale search <phrase> [repos]  search across repos
  quale fingerprint <file>       structural fingerprint
  quale clone [path]             find structural clones
  quale landmarks [path]         find unique files
  quale timeline [path]          concept history
"""

from quale.cli import main

if __name__ == "__main__":
    main()

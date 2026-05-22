#!/usr/bin/env python3
"""vocab — grammar-free structural codebase analyzer.

Usage:
  vocab analyze [path]           analyze codebase structure
  vocab diff <ref_a> <ref_b>     compare two git refs
  vocab search <phrase> [repos]  search across repos
  vocab fingerprint <file>       structural fingerprint
  vocab clone [path]             find structural clones
  vocab landmarks [path]         find unique files
  vocab timeline [path]          concept history
"""

from vocab.cli import main

if __name__ == "__main__":
    main()

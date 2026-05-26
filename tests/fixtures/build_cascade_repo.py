"""Test fixture: build a contrived git repo with a known token cascade.

Structure mimics a TypeScript dependency chain:
  types.ts ──→ auth.ts ──→ handler.ts ──→ upload.ts ───→ api.ts
                ↑                              ↑
                └─────── BadToken ──────────────┘
               (introduced in auth.ts, spreads to handler.ts, upload.ts, api.ts)

R₀ should be ~2.0-3.0 for BadToken: introduced in auth.ts, spreads to
handler.ts (gen 1), upload.ts (gen 2), api.ts (gen 3).
"""
import os
import subprocess
import tempfile
from pathlib import Path


def build_fixture(target: str | None = None) -> str:
    """Build a git repo at `target` with a known cascade. Returns path."""
    if target is None:
        target = tempfile.mkdtemp(suffix="-vocab-rho-fixture")
    
    repo = Path(target)
    repo.mkdir(parents=True, exist_ok=True)
    
    # Init git
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@vocab"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Vocab Test"], cwd=repo, capture_output=True)
    
    def commit(message: str, files: dict[str, str]):
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True)
    
    # ============ Baseline setup (commits 1-5) ============
    commit("initial: types module", {
        "src/types.ts": (
            "export type UserId = string;\n"
            "export type FilePath = string;\n"
        ),
        "README.md": "# Fixture\n",
    })
    
    commit("initial: auth module", {
        "src/auth.ts": (
            "import { type UserId } from './types';\n"
            "export function authenticate(id: UserId): boolean {\n"
            "  return id.length > 0;\n"
            "}\n"
        ),
    })
    
    commit("initial: handler module", {
        "src/handler.ts": (
            "import { authenticate } from './auth';\n"
            "export function handle(userId: string): string {\n"
            "  const ok = authenticate(userId);\n"
            "  return ok ? `handled ${userId}` : 'denied';\n"
            "}\n"
        ),
    })
    
    commit("initial: upload module", {
        "src/upload.ts": (
            "import { handle } from './handler';\n"
            "export function upload(path: string): string {\n"
            "  const result = handle('user-1');\n"
            "  return `${result} for ${path}`;\n"
            "}\n"
        ),
    })
    
    commit("initial: api entrypoint", {
        "src/api.ts": (
            "import { upload } from './upload';\n"
            "export function apiGateway(path: string): string {\n"
            "  return upload(path);\n"
            "}\n"
        ),
    })
    
    # Commit 6: normal dev, no cascade
    commit("add validation", {
        "src/validator.ts": (
            "export function validate(path: string): boolean {\n"
            "  return path.length > 0 && path.startsWith('/');\n"
            "}\n"
        ),
    })
    
    # ============ COMMIT 7: THE BIRTH OF BadToken ============
    commit("refactor auth to use token", {
        "src/auth.ts": (
            "import { type UserId } from './types';\n"
            "export function authenticate(id: UserId): boolean {\n"
            "  return id.length > 0;\n"
            "}\n"
            "// TODO: replace with BadToken pattern\n"
        ),
        "src/validator.ts": (
            "export function validate(path: string): boolean {\n"
            "  return path.length > 0 && path.startsWith('/');\n"
            "}\n"
            "// TODO: migrate to BadToken\n"
        ),
    })
    
    # ============ Token spreads: BadToken ============
    # gen 1: BadToken spreads from auth.ts to handler.ts + validator.ts
    commit("handler and validator use BadToken pattern", {
        "src/handler.ts": (
            "import { authenticate } from './auth';\n"
            "import { BadToken } from './auth';\n"
            "export function handle(userId: string): string {\n"
            "  const ok = authenticate(userId);\n"
            "  const token = new BadToken(userId);\n"
            "  return ok ? `handled ${token.value}` : 'denied';\n"
            "}\n"
        ),
        "src/validator.ts": (
            "import { BadToken } from './auth';\n"
            "export function validate(path: string): boolean {\n"
            "  return path.length > 0 && path.startsWith('/');\n"
            "}\n"
            "export function validateToken(t: BadToken): boolean {\n"
            "  return t.value.length > 0;\n"
            "}\n"
        ),
    })
    
    # gen 2: BadToken spreads to upload.ts
    commit("upload uses BadToken for auth", {
        "src/upload.ts": (
            "import { handle } from './handler';\n"
            "import { BadToken } from './auth';\n"
            "export function upload(path: string): string {\n"
            "  const token = new BadToken('session');\n"
            "  const result = handle('user-1');\n"
            "  return `${result} for ${path} (token: ${token.value})`;\n"
            "}\n"
        ),
    })
    
    # gen 3: BadToken spreads to api.ts + a new file (notifier.ts)
    commit("api and notifier adopt BadToken", {
        "src/api.ts": (
            "import { upload } from './upload';\n"
            "import { BadToken } from './auth';\n"
            "export function apiGateway(path: string): string {\n"
            "  const token = new BadToken('api');\n"
            "  return upload(path);\n"
            "}\n"
        ),
        "src/notifier.ts": (
            "import { BadToken } from './auth';\n"
            "export function notify(token: BadToken): string {\n"
            "  return `notified for ${token.value}`;\n"
            "}\n"
        ),
    })
    
    # ============ Post-cascade: contained (R₀ should drop) ============
    # BadToken stops spreading — no new files infected
    commit("refactor: consolidate BadToken usage", {
        "src/api.ts": (
            "import { upload } from './upload';\n"
            "import { BadToken } from './auth';\n"
            "import { validateToken } from './validator';\n"
            "export function apiGateway(path: string): string {\n"
            "  const token = new BadToken('api');\n"
            "  if (!validateToken(token)) return 'invalid';\n"
            "  return upload(path);\n"
            "}\n"
        ),
    })
    
    # Cleanup commit (no new token mentions)
    commit("cleanup: remove unused imports", {
        "src/handler.ts": (
            "import { authenticate, BadToken } from './auth';\n"
            "export function handle(userId: string): string {\n"
            "  const ok = authenticate(userId);\n"
            "  const token = new BadToken(userId);\n"
            "  return ok ? `handled ${token.value}` : 'denied';\n"
            "}\n"
        ),
        "src/upload.ts": (
            "import { handle } from './handler';\n"
            "import { BadToken } from './auth';\n"
            "export function upload(path: string): string {\n"
            "  const token = new BadToken('session');\n"
            "  const result = handle('user-1');\n"
            "  return `${result} for ${path} (token: ${token.value})`;\n"
            "}\n"
        ),
    })
    
    return str(repo)


if __name__ == "__main__":
    path = build_fixture()
    print(f"Fixture built at: {path}")
    subprocess.run(["git", "log", "--oneline"], cwd=path)

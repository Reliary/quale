# Changelog

## 0.9.5 — 2026-05-28

- Fixed MCP server initialization handshake error
- Updated repository description to match README tagline
- Documentation: removed em-dashes, fixed duplicate section headers

## 0.9.4 — 2026-05-28

- MCP server: `quale --mcp` exposes `edit_context`, `verify_packet`, `orient`
  as typed MCP tools (JSON-RPC over stdio, zero deps)
- MCP server: 12 unit tests covering tools/list, tools/call, error handling
- MCP config: OpenCode, Claude Desktop, Claude Code, Cursor, VS Code

## 0.9.3 — 2026-05-28

- Short aliases: `quale ec` (edit-context, 4 words), `quale vp` (verify-packet,
  4 words), `quale o` (orient, 2 characters)
- Skill file: `~/.config/opencode/skills/quale/SKILL.md` auto-loaded by OpenCode
- Trial results: skill-only 3/3 correct test file on Mistral-7B

## 0.9.2 — 2026-05-27

- CI: unified workflows, issue/PR templates, stale bot, funding
- Config: ruff, mypy, codespell in pyproject.toml
- Config: .editorconfig, .pre-commit-config.yaml

## 0.9.1 — 2026-05-27

- UIUX: fixed 5 ANSI-in-JSON bugs
- UIUX: icon constants (ICON_PRIMARY, ICON_SECONDARY, ICON_WARN, ICON_CHECK)
- UIUX: print_header() helper — consolidated 51 lines → 17
- Jargon scrub: "hub-risk + capillary" → "hub risk + connectivity",
  "Blast Radius" → "Ripple Effect", "mirror cov" → "test coverage"

## 0.9.0 — 2026-05-27

- Guardrails: CLI smoke test (72 commands), output contracts (78 assertions)
- Guardrails: JSON schema validation for agent tool format
- Guardrails: pre-commit hook, self-review CI workflow
- Unification: all path params now use `--path` flag

## 0.8.4 — 2026-05-27

- Agent orient: returns landmarks, modules, languages, workflow commands
- Review: per-file annotations, test connections, action items
- Onboard: language breakdown, macro-module filtering, hub-risk watch list
- Core templates: solve, heisenberg, trap, parity-bit — plain-English output
- CI init: accepts --path

## 0.8.3 — 2026-05-26

- Fixed migration-pairs crash, ci-trend crash, concept-flow timeout
- Fixed health-score output, extinct-exports namespace hint, check-pr empty pairs
- Core commands: solve, coupling-chain, criticality, porosity output cleanup

## 0.8.2 — 2026-05-26

- Agent edit/guard: now emit proven tool format contract instead of raw dict
- Agent commands: added --path flag
- Fixed agent routing on stale installs

## 0.8.1 — 2026-05-26

- UIUX overhaul: 17 command output templates rewritten to plain English
- Fixed 5 crash bugs (agent edit, agent guard, ci check, ci comment, ci routing)
- Jargon removal: all core commands now explain what each metric means

## 0.8.0 — 2026-05-26

- CLI restructuring: 4 personas (root, ci, agent, core)
- Human workflows: review, onboard, refactor-cost
- CI integration: pr-report --post-comment, ci-trend
- CI gates: --fail-on-hub-risk, --fail-on-clone, --fail-on-new-identifiers

## 0.7.x — earlier releases

- Initial CLI design (flat command list)
- Co-occurrence matrix engine
- Core primitives: hub-risk, capillary, spectral-gap, entropy, guard
- Session memory module

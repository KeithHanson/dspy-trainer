# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Critical Folders and Files

This section MUST remain up to date after you complete a task. 

- `AGENTS.md`: Operator and agent workflow rules for this repository.
- `.beads/`: Local beads issue database, hooks, and interaction history.
- `README.md`: Project overview and high-level goals.
- `.env.sample`: Source of truth for required environment variables.
- `docs/NAIVE_PLAN.md`: Product and architecture planning baseline.

## Using Serena tools and Exploring the code base

Serena is an extremely helpful suite of tools for working with code. 

It provides several other tools that should aid you when getting up to speed on the codebase or when completing a feature.

Start out by running the tool initial_instructions and onboarding. 

### Serena Memories

Use the list_memories, write_memory, rename_memory, delete_memory, read_memory, edit_memory tools to help yourself later. 

Good memories are things like high level overviews of code paths, documentation relevant to your tasks from within the dspy/ submodule or within our docs/ folder.

You are encouraged to use memories liberally to keep track of critical information.

### Serena LSP tools for navigating code

The find_implementations, find_symbol, find_declaration, find_referencing_symbols, get_symbols_overview, rename_symbol, replace_symbol_body, safe_delete_symbol tools are FAR better to use than grep to find things. 

If you must execute a raw search for something, utilize the search_for_pattern and find_file tools.

You will RARELY need grep when you use Serena's tools.

## RTK

Rust Token Killer has been installed as a hook in opencode. Any bash commands will first be rewritten with RTK commands so that only the pertinent information is shown to you instead of ALL the results.

Here are the most common ways I need you to use RTK below. 

You must NOT use your built-in read file/grep/find - if there is an rtk command you can use to do a task, you must use it instead of any built-in tools.

### Files

rtk ls .                        # Token-optimized directory tree
rtk read file.rs                # Smart file reading
rtk read file.rs -l aggressive  # Signatures only (strips bodies)
rtk smart file.rs               # 2-line heuristic code summary
rtk find "*.rs" .               # Compact find results
rtk grep "pattern" .            # Grouped search results
rtk diff file1 file2            # Condensed diff

### Git

rtk git status                  # Compact status
rtk git log -n 10               # One-line commits
rtk git diff                    # Condensed diff
rtk git add                     # -> "ok"
rtk git commit -m "msg"         # -> "ok abc1234"
rtk git push                    # -> "ok main"
rtk git pull                    # -> "ok 3 files +10 -2"

### GitHub CLI

rtk gh pr list                  # Compact PR listing
rtk gh pr view 42               # PR details + checks
rtk gh issue list               # Compact issue listing
rtk gh run list                 # Workflow run status

### Test Runners

rtk jest                        # Jest compact (failures only)
rtk vitest                      # Vitest compact (failures only)
rtk playwright test             # E2E results (failures only)
rtk pytest                      # Python tests (-90%)
rtk go test                     # Go tests (NDJSON, -90%)
rtk cargo test                  # Cargo tests (-90%)
rtk rake test                   # Ruby minitest (-90%)
rtk rspec                       # RSpec tests (JSON, -60%+)
rtk err <cmd>                   # Filter errors only from any command
rtk test <cmd>                  # Generic test wrapper - failures only (-90%)

### Build & Lint

rtk lint                        # ESLint grouped by rule/file
rtk lint biome                  # Supports other linters
rtk tsc                         # TypeScript errors grouped by file
rtk next build                  # Next.js build compact
rtk prettier --check .          # Files needing formatting
rtk cargo build                 # Cargo build (-80%)
rtk cargo clippy                # Cargo clippy (-80%)
rtk ruff check                  # Python linting (JSON, -80%)
rtk golangci-lint run           # Go linting (JSON, -85%)
rtk rubocop                     # Ruby linting (JSON, -60%+)

### Package Managers

rtk pnpm list                   # Compact dependency tree
rtk pip list                    # Python packages (auto-detect uv)
rtk pip outdated                # Outdated packages
rtk bundle install              # Ruby gems (strip Using lines)
rtk prisma generate             # Schema generation (no ASCII art)

### Containers

rtk docker ps                   # Compact container list
rtk docker images               # Compact image list
rtk docker logs <container>     # Deduplicated logs
rtk docker compose ps           # Compose services


### Data & Analytics

rtk json config.json            # Structure without values
rtk deps                        # Dependencies summary
rtk env -f AWS                  # Filtered env vars
rtk log app.log                 # Deduplicated logs
rtk curl <url>                  # Truncate + save full output
rtk wget <url>                  # Download, strip progress bars
rtk summary <long command>      # Heuristic summary
rtk proxy <command>             # Raw passthrough + tracking

## Beads Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
# Do not use `bd sync` in this repo (known broken)
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Version-controlled: Built on Dolt with cell-level merge
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Execution Procedures for Beads

1. **Write complete bead context before work starts**
   - Every bead description should contain enough implementation context for independent execution.
   - Reference relevant repository files directly and include related bead IDs/dependencies.
   - Include expected behavior, acceptance criteria, and verification steps in the bead description.

2. **Start each bead from a clean repository state**
   - Before beginning new bead work, ensure the git working tree is clean.
   - If there is in-progress work, commit it before starting the next bead.

3. **Test every new functionality change**
   - Any newly implemented functionality must include tests for new code paths.

4. **Completion gate before calling work done**
   - Before marking a bead done, run required tests for new code.
   - Run the full project test suite and confirm all tests pass.
   - If a feature changed code that runs in Docker containers, restart the affected containers before handing off so the operator can validate the live behavior immediately.

5. **Handle blockers with a new bead**
   - If a major blocker appears, create a dedicated blocker bead immediately.
   - Include troubleshooting context, observed failures, reproduction steps, and candidate next actions.

6. **Only close beads after test gates pass**
   - A bead is complete only when required tests exist and the relevant test suite passes.

7. **Require a Definition of Done for each epic**
   - Every epic bead should include a `Definition of Done` section in its description.
   - The section should state concrete completion criteria, including functionality delivered, test expectations, and dependency/acceptance criteria for child beads.

8. **Human validation checkpoint after each bead implementation**
   - After a subagent completes meaningful work on a bead, STOP and report results.
   - Provide exact manual verification steps for the operator to validate with their own eyes.
   - Do not begin major implementation for the next bead until the operator reviews and responds.
   - Unit tests are necessary but not sufficient; operator UX review is required before proceeding.

9. **Commit and push cadence (per bead)**
   - After the agent verifies bead changes locally (tests/build pass), commit and push to keep the repo clean.
   - Perform this commit/push before operator polish review unless the operator explicitly asks to batch multiple beads.
   - Keep commits scoped and readable; avoid carrying unrelated changes between beads.

10. **Environment variable and onboarding docs policy**
   - Any change that introduces, removes, or alters environment variables MUST update `.env.sample` in the same bead.
   - The `README.md` MUST be updated whenever behavior, setup, commands, architecture, or required configuration changes.
   - README should always be accurate for a fresh clone and first-time setup.

### Auto-Sync

bd automatically syncs with git:

- Exports to `.beads/issues.jsonl` after changes (5s debounce)
- Imports from JSONL when newer (e.g., after `git pull`)
- No manual export/import needed!

Note: `bd sync` is known broken in this repo. Do not run it. Use available `bd` commands and continue the required git push workflow.

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- END BEADS INTEGRATION -->

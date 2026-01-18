# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monco is a Discord bot that integrates Claude Agent SDK to provide full Claude Code capabilities through Discord slash commands. Each user gets an isolated workspace with automatic session management, size limits, and cleanup.

## Architecture

### Core Components

1. **UserSession Class** (src/main.py:69-199)
   - Manages isolated workspaces per user using UUID-based directories
   - Each session has its own ClaudeSDKClient instance with persistent connection
   - Tracks workspace size, session expiry, cost, and task count
   - Handles initialization of Claude Code directory structure (.claude/, skills/, commands/)

2. **Session Management** (src/main.py:201-254)
   - Global `user_sessions` dict maps Discord user IDs to UserSession instances
   - Background cleanup task runs every CLEANUP_INTERVAL_HOURS to remove expired sessions
   - Sessions expire after SESSION_EXPIRY_HOURS of inactivity
   - Session state persists across bot restarts via workspace filesystem

3. **Command Execution** (src/main.py:259-450)
   - `execute_with_session()` handles all Claude Agent SDK interactions
   - Checks workspace size limits before execution
   - Streams messages from SDK and logs tool usage
   - Tracks execution metadata (turns, duration, cost)
   - Enforces workspace size limit of MAX_WORKSPACE_SIZE_MB

4. **Discord Commands**
   - `/run` - Execute any Claude task in user's workspace
   - `/code` - Two-phase workflow: generate code project, then upload to GitHub
   - `/status` - Show session info, workspace usage, expiry time
   - `/cleanup` - Free space by deleting workspace files (keeps .claude/ config)
   - `/reset` - Create new session with fresh workspace

### Key Paths

- **PROJECT_ROOT**: `src/` directory (where main.py lives)
- **PLUGIN_PATH**: `claude-code/plugin-template/` (Claude Code plugins, skills, commands)
- **WORKPLACE_ROOT**: `workplace/` (parent directory for all user workspaces)
- **User Workspace**: `workplace/{session_uuid}/` (isolated per user)

### Plugin System

The bot uses Claude Code's plugin system (claude-code/plugin-template/):

**Enabled Plugins** (settings.json):
- `ralph-wiggum@claude-plugins-official` - Iterative task execution (used by `/code`)
- `github@claude-plugins-official` - GitHub integration
- `commit-commands@claude-plugins-official` - Git commit helpers
- `typescript-lsp`, `rust-analyzer-lsp`, `clangd-lsp` - Language server support
- `code-review@claude-plugins-official` - Code review capabilities
- Plus: agent-sdk-dev, context7, security-guidance, plugin-dev, slack

**Custom Commands**:
- `/ralph-loop` - Starts iterative execution loop (see Phase 1 of `/code`)
- `/cancel-ralph` - Cancels active ralph-loop by removing `.claude/ralph-loop.local.md`

**Available Skills**:
- Document generation: docx, pdf, pptx, xlsx
- Design: canvas-design, frontend-design, brand-guidelines, algorithmic-art
- Development: mcp-builder, skill-creator, code-review
- Testing: webapp-testing

### Claude Agent SDK Integration

Each UserSession initializes ClaudeSDKClient with:
- **allowed_tools**: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Task
- **permission_mode**: "dontAsk" (no interactive prompts)
- **cwd**: User's isolated workspace path
- **plugins**: Local plugin from PLUGIN_PATH
- **add_dirs**: Restricted to user's workspace only
- **setting_sources**: ["project"] enables Claude Code features

### /code Command Workflow

The `/code` command uses a two-phase approach to generate and publish code projects:

**Phase 1: Ralph Loop Generation** (src/main.py:611-642)
- Invokes `/kaodean-plugin:ralph-loop` command from the plugin system
- Ralph Loop is an iterative execution pattern where Claude continues working until completion
- Uses `--completion-promise "DONE!!!"` - Claude can only exit when genuinely complete
- Uses `--max-iterations` parameter (default: 50) to prevent infinite loops
- Creates `.claude/ralph-loop.local.md` to track iteration state
- Each iteration sees previous work in files/git history for incremental improvement
- Sends Discord progress updates every 5 minutes to keep interaction alive

**Phase 2: GitHub Upload** (src/main.py:645-706)
- After successful generation, automatically uploads to GitHub
- Repo naming: `project-{session_uuid[:8]}` (8-char UUID prefix)
- Handles both fresh projects (git init) and existing repos (git push)
- Uses `gh` CLI for repo creation (requires gh authentication)
- Extracts repo URL via regex: looks for `REPO_URL:` marker or github.com URLs
- Fallback username: `kaodean` if URL extraction fails

## Development Commands

### Environment Setup

```bash
# Install dependencies
uv sync

# Create .env from template
cp .env.template .env
# Edit .env with your DISCORD_BOT_TOKEN
```

### Run Bot Locally

```bash
# Using uv (recommended)
uv run src/main.py

# Traditional method
cd src
python main.py
```

### Docker Deployment

```bash
# Build image
docker build -t monco .

# Run container
docker run -d --env-file .env monco
```

**Dockerfile Notes:**
- Uses Python 3.11-slim base
- Installs Node.js 18.x (required for Claude Code CLI)
- Installs Claude Code CLI via curl script
- Uses uv for dependency management
- Sets PLUGIN_PATH and WORKPLACE_ROOT environment variables

### Testing

No formal test suite exists. Manual testing via Discord commands:
1. Use `/run` with simple tasks to verify session creation
2. Use `/status` to check workspace state
3. Use `/cleanup` to test workspace management
4. Use `/code` to test full project generation workflow

**Prerequisites for /code command:**
- `gh` CLI installed and authenticated (`gh auth login`)
- Git configured with user.name and user.email

## Configuration

Required environment variables:
- `DISCORD_BOT_TOKEN` - Discord bot token (required)

Optional environment variables (defaults in src/main.py:45-47):
- `MAX_WORKSPACE_SIZE_MB` - Workspace size limit (default: 500MB)
- `SESSION_EXPIRY_HOURS` - Session expiry time (default: 24h)
- `CLEANUP_INTERVAL_HOURS` - Cleanup interval (default: 1h)
- `PLUGIN_PATH` - Custom plugin path (default: claude-code/plugin-template)
- `WORKPLACE_ROOT` - Custom workplace directory (default: workplace/)
- `ANTHROPIC_API_KEY` - Anthropic API key (optional, Claude Agent SDK may use default)

## Important Implementation Details

### Session Lifecycle
- Sessions are created lazily on first `/run` or `/code` command
- Each session gets unique UUID and isolated workspace directory
- Background cleanup task removes expired sessions and their workspaces
- Sessions persist across commands but expire after inactivity

### Workspace Isolation
- Each user gets isolated workspace at `workplace/{session_uuid}/`
- Claude Agent SDK is configured with `add_dirs` to restrict file access
- Workspace includes `.claude/` directory for Claude Code features
- `CLAUDE.md` file in workspace provides context to Claude about session limits

### Discord Interaction Handling
- Commands respond immediately to avoid 3-second timeout
- Long-running tasks use `followup.send()` to send updates
- `/code` command sends progress updates every 5 minutes during generation
- Output is chunked to fit Discord's 2000 character message limit (chunks of 1900 chars)

### Message Streaming Pattern

The `execute_with_session()` function (src/main.py:323-429) uses async iteration to process SDK messages:

```python
async for message in session.client.receive_messages():
    if isinstance(message, SystemMessage):
        # System events (init, status updates)
    elif isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                # Claude's text output
            elif isinstance(block, ToolUseBlock):
                # Tool invocation (logs tool name, input)
            elif isinstance(block, ToolResultBlock):
                # Tool results (shows preview)
    elif isinstance(message, ResultMessage):
        # Final result with metadata (cost, turns, duration)
        break
```

This pattern enables:
- Real-time logging of tool usage during execution
- Cost tracking from ResultMessage
- Verbose output showing Claude's reasoning and actions
- Early termination on errors via `message.is_error`

### Cost and Usage Tracking
- Each session tracks `total_cost_usd` from ResultMessage
- `total_tasks` counts number of completed executions
- Stats shown in `/status` command
- No persistent storage - resets when session expires

## Known Patterns

### Error Handling
- All commands wrapped in try/except with logging
- Errors sent to user via Discord followup messages
- Session cleanup errors are logged but don't fail the operation

### Logging
- Uses custom format with level=INFO
- Logs command details (user, guild, prompt)
- Logs session lifecycle events (create, cleanup, expiry)
- Logs execution statistics (turns, duration, cost, tools used)

### GitHub Integration
- Uses `gh` CLI for repo creation (must be installed and authenticated)
- Repo name pattern: `project-{session_uuid[:8]}`
- Extracts URL using regex: `REPO_URL:` marker or `https://github.com/...`
- Handles both new repos and existing repos with git history

## Platform Notes

- Project expects Linux/WSL environment (per README.md:23)
- Uses pathlib.Path for cross-platform path handling
- Discord bot requires no privileged intents (only guilds + slash commands)
- Workspace cleanup uses shutil.rmtree for directory removal

## Dependencies

**Core Dependencies** (pyproject.toml):
- `claude-agent-sdk>=0.1.18` - Claude Agent SDK for programmatic Claude access
- `discord-py>=2.6.4` - Discord bot framework
- `python-dotenv>=1.2.1` - Environment variable management

**External Tools Required**:
- `gh` CLI - GitHub integration (for `/code` command)
- `git` - Version control (initialized in user workspaces)
- `claude` CLI - Claude Code command-line interface (installed by Dockerfile)
- Node.js 18.x - Required by Claude Code CLI (installed by Dockerfile)

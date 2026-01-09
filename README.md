# Monco

## Features

- 🤖 **Claude Agent Integration** - Full Claude Code features with skills, commands, and project memory
- 📁 **Workspace Management** - Automatic size limits and cleanup
- ⏱️ **Session Lifecycle** - Auto-expiry and background cleanup
- 💰 **Cost Tracking** - Monitor API usage per session
- 🔧 **Flexible Configuration** - Customize via environment variables

## Usage

### 1. Prerequisites

- Python 3.10+
- Discord Bot Token
- Anthropic API Key (optional, if not using default)

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/kaodean/monco.git
cd monco

# Install dependencies with uv (recommended)
uv sync

```

### 3. Configuration

```bash
# Copy template and configure
cp src/.env.template src/.env
# Edit src/.env with your tokens
```

**Required:**
- `DISCORD_BOT_TOKEN` - Your Discord bot token

**Optional:**
- `MAX_WORKSPACE_SIZE_MB` - Workspace size limit (default: 500MB)
- `SESSION_EXPIRY_HOURS` - Session expiry time (default: 24h)
- `CLEANUP_INTERVAL_HOURS` - Cleanup check interval (default: 1h)
- `PLUGIN_PATH` - Custom plugin path
- `WORKPLACE_ROOT` - Custom workplace directory

### 4. Run

```bash
# Using uv (recommended)
uv run src/main.py

# Or traditional way
cd src
python main.py
```

## Discord Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/run` | Execute tasks with Claude Agent |
| `/code` | Generate code projects |
| `/status` | Check session info and workspace usage |
| `/cleanup` | Free up workspace space |
| `/reset` | Create new session |

## Documentation

- [Quick Start Guide](QUICK_START_GUIDE.md) - Detailed usage guide
- [Deployment Checklist](DEPLOYMENT_CHECKLIST.md) - Production deployment guide

## License

[LICENSE](./LICENSE)
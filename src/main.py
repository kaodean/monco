import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import discord
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
    ToolResultBlock,
    TextBlock,
)
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Setup logging with custom format
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
# Load .env file
load_dotenv()

# -----------------------------
# Project Paths
# ----------------------------
PROJECT_ROOT = Path(__file__).parent.absolute()
PLUGIN_PATH = Path(os.getenv("PLUGIN_PATH", str(PROJECT_ROOT.parent / "claude-code" / "plugin-template")))
WORKPLACE_ROOT = Path(os.getenv("WORKPLACE_ROOT", str(PROJECT_ROOT.parent / "workplace")))

# -----------------------------
# Workspace Management Settings
# -----------------------------
MAX_WORKSPACE_SIZE_MB = int(os.getenv("MAX_WORKSPACE_SIZE_MB", "50"))
SESSION_EXPIRY_HOURS = int(os.getenv("SESSION_EXPIRY_HOURS", "24"))
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "1"))

# -----------------------------
# Discord Configuration
# -----------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    logger.error("[!] DISCORD_BOT_TOKEN not set in environment variables.")
    exit(1)

# Use minimal intents (no Privileged Intents needed)
intents = discord.Intents.default()
intents.message_content = False  # Only use slash commands, no message content needed
intents.guilds = True

bot = commands.Bot(command_prefix="/", intents=intents)


# -----------------------------
# User Session Management
# -----------------------------
class UserSession:
    """Manage each user's Claude SDK Client and workspace"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.session_uuid = str(uuid.uuid4())
        self.workspace_path = WORKPLACE_ROOT / self.session_uuid
        self.client: Optional[ClaudeSDKClient] = None
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.total_cost_usd = 0.0
        self.total_tasks = 0
        
    async def initialize(self, allowed_tools: Optional[List[str]] = None, permission_mode: str = "dontAsk"):
        """Initialize ClaudeSDKClient and workspace"""
        # Create workspace directory
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[+] Created workspace for user {self.user_id}: {self.workspace_path}")
        
        # Create Claude Code directory structure
        claude_dir = self.workspace_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "skills").mkdir(exist_ok=True)
        (claude_dir / "commands").mkdir(exist_ok=True)
        
        # Create CLAUDE.md memory file
        claude_md = self.workspace_path / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                f"# Project Context\n\n"
                f"This is your personal workspace (Session UUID: {self.session_uuid}).\n\n"
                f"## Workspace Information\n"
                f"- Created: {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"- Max Size: {MAX_WORKSPACE_SIZE_MB}MB\n"
                f"- Session Expiry: {SESSION_EXPIRY_HOURS} hours\n\n"
                f"## Guidelines\n"
                f"- Keep files organized\n"
                f"- Clean up temporary files when done\n"
                f"- Use meaningful file and directory names\n"
            )
        
        # Default toolset
        if allowed_tools is None:
            allowed_tools = [
                "Read", "Write", "Edit",
                "Bash",
                "Glob", "Grep",
                "WebSearch", "WebFetch",
                "Task"
            ]
        
        # Configure options with Claude Code features
        options = ClaudeAgentOptions(
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            cwd=str(self.workspace_path),  # Restrict to this workspace
            plugins=[{"type": "local", "path": str(PLUGIN_PATH)}],
            add_dirs=[str(self.workspace_path)],  # Only allow access to this directory
            setting_sources=["project"]  # Enable Claude Code features
        )
        
        # Initialize client
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        logger.info(f"[+] Initialized ClaudeSDKClient for user {self.user_id} with Claude Code features")
        
    async def reset(self, allowed_tools: Optional[List[str]] = None, permission_mode: str = "dontAsk"):
        """Reset session - disconnect old connection, create new workspace"""
        # Disconnect old connection
        if self.client:
            try:
                await self.client.disconnect()
                logger.info(f"[+] Disconnected old session for user {self.user_id}")
            except Exception as e:
                logger.warning(f"[!] Error disconnecting old session: {e}")
        
        # Generate new UUID and workspace
        self.session_uuid = str(uuid.uuid4())
        self.workspace_path = WORKPLACE_ROOT / self.session_uuid
        self.created_at = datetime.now()
        
        # Re-initialize
        await self.initialize(allowed_tools, permission_mode)
        
    async def cleanup(self):
        """Cleanup resources"""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"[!] Error during cleanup: {e}")
    
    def calculate_workspace_size(self) -> float:
        """Calculate workspace size in MB"""
        total_size = 0
        try:
            for file_path in self.workspace_path.rglob('*'):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except Exception as e:
            logger.warning(f"[!] Error calculating workspace size: {e}")
        return total_size / (1024 * 1024)  # Conㄙt to MB
    
    def check_workspace_size_limit(self) -> tuple[bool, float]:
        """Check if workspace size is within limit"""
        size_mb = self.calculate_workspace_size()
        is_within_limit = size_mb < MAX_WORKSPACE_SIZE_MB
        return is_within_limit, size_mb
    
    def is_expired(self) -> bool:
        """Check if session has expired"""
        expiry_time = self.last_used + timedelta(hours=SESSION_EXPIRY_HOURS)
        return datetime.now() > expiry_time
    
    async def cleanup_workspace_files(self, keep_config: bool = True):
        """Clean up workspace files, optionally keeping .claude/ directory"""
        try:
            for item in self.workspace_path.iterdir():
                if keep_config and item.name in [".claude", "CLAUDE.md"]:
                    continue
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            logger.info(f"[+] Cleaned workspace for user {self.user_id}")
        except Exception as e:
            logger.warning(f"[!] Error cleaning workspace: {e}")
                
    def update_last_used(self):
        """Update last used timestamp"""
        self.last_used = datetime.now()


# Global session management
user_sessions: Dict[int, UserSession] = {}


async def get_or_create_session(user_id: int) -> UserSession:
    """Get or create user session"""
    if user_id not in user_sessions:
        session = UserSession(user_id)
        await session.initialize()
        user_sessions[user_id] = session
        logger.info(f"[+] Created new session for user {user_id}")
    
    session = user_sessions[user_id]
    session.update_last_used()
    return session


async def cleanup_expired_sessions():
    """Background task to clean up expired sessions"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)  # Convert hours to seconds
            
            logger.info("[*] Running expired session cleanup...")
            expired_sessions = []
            
            for user_id, session in list(user_sessions.items()):
                if session.is_expired():
                    expired_sessions.append((user_id, session))
            
            for user_id, session in expired_sessions:
                try:
                    # Cleanup session
                    await session.cleanup()
                    
                    # Remove workspace directory
                    if session.workspace_path.exists():
                        shutil.rmtree(session.workspace_path)
                    
                    # Remove from active sessions
                    del user_sessions[user_id]
                    
                    logger.info(f"[+] Cleaned up expired session for user {user_id} (UUID: {session.session_uuid})")
                except Exception as e:
                    logger.error(f"[!] Error cleaning up session for user {user_id}: {e}")
            
            if expired_sessions:
                logger.info(f"[+] Cleanup completed: {len(expired_sessions)} session(s) removed")
            else:
                logger.info("[*] No expired sessions found")
                
        except Exception as e:
            logger.error(f"[!] Error in cleanup task: {e}")


# -----------------------------
# Claude Agent SDK Execution Function (using persistent Client)
# -----------------------------
async def execute_with_session(
    session: UserSession,
    prompt: str,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Execute tasks using user's persistent ClaudeSDKClient
    
    Args:
        session: UserSession instance
        prompt: Task description to execute
        verbose: Whether to include detailed execution information
    
    Returns:
        Dict containing execution results
    """
    output_lines = []  # For user-facing output
    claude_responses = []  # Store only Claude's text responses
    tools_used = []
    errors = []
    metadata = {
        "start_time": datetime.now().isoformat(),
        "session_uuid": session.session_uuid,
        "workspace_path": str(session.workspace_path),
        "total_turns": 0,
        "duration_ms": 0,
        "duration_api_ms": 0,
        "total_cost_usd": None,
    }
    
    try:
        # Check workspace size before execution
        is_within_limit, size_mb = session.check_workspace_size_limit()
        if not is_within_limit:
            error_msg = (
                f"Workspace size limit exceeded: {size_mb:.1f}MB / {MAX_WORKSPACE_SIZE_MB}MB\\n"
                f"Please use /cleanup to free up space or /reset to start fresh."
            )
            errors.append({
                "type": "workspace_size_limit",
                "message": error_msg
            })
            output_lines.append(f"[!] {error_msg}")
            return {
                "output": "\\n".join(output_lines),
                "success": False,
                "metadata": metadata,
                "tools_used": tools_used,
                "errors": errors,
            }
        
        # Log detailed information
        if verbose:
            logger.info("=" * 60)
            logger.info(f"Monco (claude-code) Execution Started")
            logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Session UUID: {session.session_uuid}")
            logger.info(f"Task: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
            logger.info(f"Workspace: {session.workspace_path}")
            logger.info(f"Workspace Size: {size_mb:.1f}MB / {MAX_WORKSPACE_SIZE_MB}MB")
            logger.info("=" * 60)
        
        # Send prompt to Claude agent
        await session.client.query(prompt)
        
        # Receive all messages
        async for message in session.client.receive_messages():
            # Handle System Messages
            if isinstance(message, SystemMessage):
                if verbose and message.subtype != "init":
                    logger.info(f"System: {message.subtype}")
            
            # Handle Assistant Messages
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    # Text content
                    if isinstance(block, TextBlock):
                        text = block.text.strip()
                        if text:
                            claude_responses.append(text)  # Store for user output
                            if verbose:
                                logger.info(f"Claude: {text}")
                    
                    # Tool usage
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_input = block.input
                        tools_used.append({
                            "name": tool_name,
                            "id": block.id,
                            "input": tool_input
                        })
                        
                        if verbose:
                            logger.info("─" * 40)
                            logger.info(f"[*] TOOL: {tool_name}")
                            
                            # Show relevant input details
                            if tool_name == "Bash":
                                cmd = tool_input.get("command", "")
                                logger.info(f"    └─> Command: {cmd[:100]}{'...' if len(cmd) > 100 else ''}")
                            elif tool_name in ["Read", "Write", "Edit"]:
                                file_path = tool_input.get("file_path", "")
                                logger.info(f"    └─> File: {file_path}")
                            elif tool_name == "WebSearch":
                                query_text = tool_input.get("query", "")
                                logger.info(f"    └─> Search: {query_text}")
                            elif tool_name == "WebFetch":
                                url = tool_input.get("url", "")
                                logger.info(f"    └─> URL: {url}")
                            elif tool_name == "Task":
                                subagent = tool_input.get("subagent_type", "")
                                description = tool_input.get("description", "")
                                logger.info(f"    └─> Subagent: {subagent}")
                                logger.info(f"    └─> Description: {description}")
                    
                    # Tool results
                    elif isinstance(block, ToolResultBlock):
                        if verbose and hasattr(block, "content") and block.content:
                            if isinstance(block.content, str):
                                result_preview = block.content[:150]
                                logger.info(f"    [+] Result: {result_preview}{'...' if len(block.content) > 150 else ''}")
                            logger.info("─" * 40)
            
            # Handle Result Messages
            elif isinstance(message, ResultMessage):
                metadata["total_turns"] = message.num_turns
                metadata["duration_ms"] = message.duration_ms
                metadata["duration_api_ms"] = message.duration_api_ms
                metadata["total_cost_usd"] = message.total_cost_usd
                
                # Update session statistics
                session.total_tasks += 1
                if message.total_cost_usd:
                    session.total_cost_usd += message.total_cost_usd
                
                # Log detailed statistics
                if verbose:
                    logger.info("=" * 60)
                    logger.info(f"{'+' if not message.is_error else '-'} Execution Completed: {message.subtype}")
                    logger.info("")
                    logger.info("Execution Statistics:")
                    logger.info(f"   Conversation Turns: {message.num_turns}")
                    logger.info(f"   Total Execution Time: {message.duration_ms/1000:.2f} sec")
                    logger.info(f"   API Time: {message.duration_api_ms/1000:.2f} sec")
                    if message.total_cost_usd:
                        logger.info(f"   Total Cost: ${message.total_cost_usd:.4f} USD")
                    logger.info(f"   Tools Used: {len(tools_used)}")
                    
                    if tools_used:
                        tool_counts = {}
                        for tool in tools_used:
                            tool_name = tool["name"]
                            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                        logger.info(f"   Tool Usage Details:")
                        for tool_name, count in sorted(tool_counts.items()):
                            logger.info(f"     - {tool_name}: {count} times")
                    
                    logger.info("=" * 60)
                
                # Prepare user-facing output (only Claude's responses)
                if claude_responses:
                    output_lines.extend(claude_responses)
                else:
                    output_lines.append("[No response from Claude]")
                
                metadata["end_time"] = datetime.now().isoformat()
                metadata["status"] = "error" if message.is_error else "success"
                
                if message.is_error:
                    errors.append({
                        "type": "execution_error",
                        "message": message.subtype
                    })
                
                break
        
        success = len(errors) == 0
        
    except Exception as e:
        errors.append({
            "type": "exception",
            "message": str(e)
        })
        success = False
        logger.error(f"[!] Error occurred: {str(e)}")
        output_lines.append(f"Error: {str(e)}")
        metadata["end_time"] = datetime.now().isoformat()
        metadata["status"] = "exception"
    
    return {
        "output": "\n".join(output_lines),
        "success": success,
        "metadata": metadata,
        "tools_used": tools_used,
        "errors": errors,
    }


# -----------------------------
# Bot Startup Event
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    
    # Start background cleanup task
    bot.loop.create_task(cleanup_expired_sessions())
    
    logger.info("=" * 60)
    logger.info("[+] Monco Bot Ready")
    logger.info(f"[+] Logged in as: {bot.user}")
    logger.info(f"[+] Bot ID: {bot.user.id}")
    logger.info(f"[+] Connected to {len(bot.guilds)} guild(s)")
    logger.info(f"[+] Workplace Root: {WORKPLACE_ROOT}")
    logger.info(f"[+] Max Workspace Size: {MAX_WORKSPACE_SIZE_MB}MB")
    logger.info(f"[+] Session Expiry: {SESSION_EXPIRY_HOURS} hours")
    logger.info(f"[+] Cleanup Interval: {CLEANUP_INTERVAL_HOURS} hour(s)")
    logger.info("[+] Background cleanup task started")
    logger.info("=" * 60)


# -----------------------------
# Bot Shutdown Event
# -----------------------------
@bot.event
async def on_close():
    logger.info("=" * 60)
    logger.info("[*] Bot shutting down - cleaning up sessions...")
    
    # Clean up all user sessions
    for user_id, session in user_sessions.items():
        try:
            await session.cleanup()
            logger.info(f"[+] Cleaned up session for user {user_id}")
        except Exception as e:
            logger.warning(f"[!] Error cleaning up session for user {user_id}: {e}")
    
    logger.info("[+] All sessions cleaned up")
    logger.info("=" * 60)


# -----------------------------
# `/help` Command
# -----------------------------
@bot.tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: discord.Interaction):
    logger.info(f"[*] /help command requested by {interaction.user}")
    help_text = (
        "## Monco Command Overview\n\n"
        "### /help\n"
        "Show all available commands and descriptions\n"
        "```\n/help\n```\n"
        "### /run\n"
        "Execute Claude Agent tasks in your persistent workspace\n"
        "- **prompt** (required): Task description to execute\n"
        '```\n/run prompt:"Check today\'s weather"\n```\n'
        "### /code\n"
        "Let Monco generate a code project in your workspace\n"
        "- **prompt** (required): Project description\n"
        '```\n/code prompt:"Create a Flask API project with user authentication"\n```\n'
        "### /reset\n"
        "Reset your Claude session and create a new workspace\n"
        "```\n/reset\n```\n"
        "### /status\n"
        "Check your current session information, workspace size, and expiry time\n"
        "```\n/status\n```\n"
        "### /cleanup\n"
        "Clean up your workspace to free up space\n"
        "- **delete_all** (optional): Delete entire workspace (default: keep .claude/ config)\n"
        "```\n/cleanup\n/cleanup delete_all:True\n```\n\n"
        f"**Workspace Limits:**\n"
        f"- Max Size: {MAX_WORKSPACE_SIZE_MB}MB\n"
        f"- Session Expiry: {SESSION_EXPIRY_HOURS} hours of inactivity\n"
    )
    await interaction.response.send_message(help_text, ephemeral=True)


# -----------------------------
# `/run` Command
# -----------------------------
@bot.tree.command(name="run", description="Execute Claude Agent tasks in your persistent workspace")
@app_commands.describe(prompt="Task description to execute")
async def run(interaction: discord.Interaction, prompt: str):
    logger.info("=" * 60)
    logger.info(f"[*] Received command: /run prompt=\"{prompt}\"")
    logger.info(f"[*] User: {interaction.user} (ID: {interaction.user.id})")
    logger.info(f"[*] Guild: {interaction.guild.name if interaction.guild else 'DM'}")
    
    # Respond to Discord first to avoid timeout
    await interaction.response.send_message(f"🫡 We'll get started on your task shortly...\n**Task:** {prompt[:100]}...")
    
    try:
        # Get or create user session
        session = await get_or_create_session(interaction.user.id)
        
        # Execute using persistent client
        result = await execute_with_session(session, prompt, verbose=True)
        
        # Get output content
        output_text = result["output"]
        success = result["success"]
        metadata = result["metadata"]
        
        # Format simple response for user
        if success:
            response = output_text
        else:
            response = f"We encountered some issues while doing your task:\n{output_text}"
        
        # Send results in chunks (Discord has 2000 character limit)
        if len(response) > 1900:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            for chunk in chunks:
                await interaction.followup.send(chunk)
        else:
            await interaction.followup.send(response)
        
        logger.info(f"[+] /run command completed successfully" if success else f"[-] /run command failed")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"[!] /run command error: {str(e)}", exc_info=True)
        logger.info("=" * 60)
        await interaction.followup.send(f"[ERROR] Execution failed: {str(e)}")


# -----------------------------
# `/code` Command
# -----------------------------
@bot.tree.command(name="code", description="Let Monco generate a code project in your workspace")
@app_commands.describe(
    prompt="Project description (e.g., Create a Flask API project)",
    max_iterations="Maximum iterations for project generation (default: 50)"
)
async def code(
    interaction: discord.Interaction, 
    prompt: str,
    max_iterations: int = 50
):
    import re
    
    logger.info("=" * 60)
    logger.info(f"[*] Received command: /code prompt=\"{prompt}\" max_iterations={max_iterations}")
    logger.info(f"[*] User: {interaction.user} (ID: {interaction.user.id})")
    logger.info(f"[*] Guild: {interaction.guild.name if interaction.guild else 'DM'}")
    
    await interaction.response.send_message(f"🫡 We'll get started on your task shortly...\n**Task:** {prompt[:100]}...")
    
    # Helper function to send progress updates
    async def send_progress_update(message: str):
        try:
            await interaction.followup.send(message)
        except discord.errors.HTTPException as e:
            logger.warning(f"[!] Failed to send progress update: {e}")
    
    try:
        # Get or create user session
        session = await get_or_create_session(interaction.user.id)
        
        # ============================================
        # Phase 1: Generate Project
        # ============================================
        logger.info("[*] Phase 1: Generating project...")
        
        generation_prompt = f"""
/kaodean-plugin:ralph-loop \"{prompt}\" --completion-promise \"DONE!!!\" --max-iterations {max_iterations}
"""
        
        # Execute generation with progress tracking
        logger.info("[*] Starting project generation...")
        execution_task = asyncio.create_task(
            execute_with_session(session, generation_prompt, verbose=True)
        )
        
        # Progress update loop (every 5 minutes)
        start_time = asyncio.get_event_loop().time()
        update_interval = 300  # 5 minutes
        last_update = start_time
        
        while not execution_task.done():
            await asyncio.sleep(10)  # Check every 10 seconds
            current_time = asyncio.get_event_loop().time()
            
            if current_time - last_update >= update_interval:
                elapsed_minutes = int((current_time - start_time) / 60)
                await send_progress_update(f"😴 We're still working on it... ({elapsed_minutes} minutes elapsed)")
                last_update = current_time
        
        # Check generation result
        generation_result = await execution_task
        
        if not generation_result["success"]:
            await send_progress_update("😔 We did our best.. but we can't complete this task.")
            logger.warning("[-] Phase 1 failed: Project generation unsuccessful")
            logger.info("=" * 60)
            return
        
        logger.info("[+] Phase 1 completed: Project generated successfully")
        
        # ============================================
        # Phase 2: Get Project Name
        # ============================================
        logger.info("[*] Phase 2: Identifying project name...")
        
        project_name_prompt = """What is the name of the project you just created? 

IMPORTANT: 
1. Respond with ONLY the project name in English
2. Use lowercase letters, numbers, hyphens only
3. No spaces, no special characters
4. Format: my-project-name
5. Do not include any explanation or additional text

Project name:"""
        
        project_name_result = await execute_with_session(session, project_name_prompt, verbose=True)
        
        # Extract project name from response
        project_name_raw = project_name_result.get("output", "").strip()
        logger.info(f"[*] Raw project name response: {project_name_raw}")
        
        # Clean up the response - extract only valid project name
        # Remove markdown, quotes, and extra text
        project_name = re.sub(r'[`"\'\*]', '', project_name_raw)
        # Extract first valid line that looks like a project name
        lines = [line.strip() for line in project_name.split('\n') if line.strip()]
        project_name = lines[0] if lines else ""
        
        # Remove common prefixes
        project_name = re.sub(r'^(project name:|name:|the project is called:?|project:?)\s*', '', project_name, flags=re.IGNORECASE)
        project_name = project_name.strip()
        
        # Take only first word if multiple words
        project_name = re.split(r'[\s,;]', project_name)[0]
        
        # Ensure GitHub compatible format (lowercase, alphanumeric and hyphens only)
        project_name = re.sub(r'[^a-zA-Z0-9_-]', '-', project_name.lower())
        project_name = re.sub(r'-+', '-', project_name).strip('-')
        
        # Validate length and format
        if not project_name or len(project_name) < 3 or not re.match(r'^[a-z0-9][a-z0-9_-]*[a-z0-9]$', project_name):
            # Fallback to timestamp-based name
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            project_name = f"project-{timestamp}"
            logger.warning(f"[!] Invalid project name, using fallback: {project_name}")
        
        logger.info(f"[+] Phase 2 completed: Project name identified as '{project_name}'")
        await send_progress_update(f"📝 We decided to name your project **{project_name}**.")
        
        # ============================================
        # Phase 3: Upload to GitHub
        # ============================================
        logger.info("[*] Phase 3: Uploading to GitHub...")
        await send_progress_update(f"📤 We'll upload this project to github! \n - project name: {project_name}")
        
        github_prompt = f"""
    Please upload this project to GitHub. Follow these steps carefully:

    1. Check git status: git status
    2. If NOT a git repo (command fails):
       - Initialize: git init
       - Add files: git add .
       - Initial commit: git commit -m "Initial commit: {prompt[:50]}"
       - Create repo and push: gh repo create {project_name} --source=. --public --push
    3. If IS a git repo:
       - Check remote: git remote -v
       - If no remote: gh repo create {project_name} --source=. --public --push
       - If remote exists: git add . && git commit -m "Update: {prompt[:50]}" && git push

    CRITICAL: After success, run this command and output the result:
    git remote get-url origin

    Output format:
    REPO_URL: <the full https://github.com/username/repo-name URL>
    """
        
        github_result = await execute_with_session(session, github_prompt, verbose=True)
        
        if not github_result["success"]:
            await send_progress_update("⚠️ Project generated successfully, but GitHub upload failed. Check logs for details.")
            logger.warning("[-] Phase 3 failed: GitHub upload unsuccessful")
            logger.info("=" * 60)
            return
        
        # ============================================
        # Phase 4: Extract Repository URL
        # ============================================
        logger.info("[*] Phase 4: Extracting repository URL...")
        
        output = github_result.get("output", "")
        repo_url = None
        
        # Try to find REPO_URL: marker
        repo_url_match = re.search(r'REPO_URL:\s*(https://github\.com/[^\s\n]+)', output, re.IGNORECASE)
        if repo_url_match:
            repo_url = repo_url_match.group(1).rstrip('/')
            logger.info(f"[+] Found repo URL via marker: {repo_url}")
        
        # Fallback: Search for any GitHub URL
        if not repo_url:
                url_match = re.search(r'https://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+?)(?:\.git)?(?:\s|$|/)', output)
                if url_match:
                    username = url_match.group(1)
                    reponame = url_match.group(2)
                    repo_url = f"https://github.com/{username}/{reponame}"
                    logger.info(f"[+] Found repo URL via pattern: {repo_url}")
        if not repo_url:
            await send_progress_update("⚠️ Project uploaded to GitHub, but we couldn't retrieve the repository URL. Please check manually.")
            logger.warning("[-] Phase 4 failed: Could not extract repository URL")
            logger.info("=" * 60)
            return
        # ============================================
        await send_progress_update(
            f"✅ **Project successfully uploaded to GitHub!**\n"
            f"🔗 Repository: {repo_url}\n"
            f"📦 Project: **{project_name}**"
        )
        
        logger.info(f"[+] Phase 4 completed: Repository URL extracted")
        logger.info(f"[+] All phases completed successfully!")
        logger.info(f"[+] Repository: {repo_url}")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"[!] /code command error: {str(e)}", exc_info=True)
        logger.info("=" * 60)
        try:
            await interaction.followup.send(f"❌ **Error:** Generation failed\n```{str(e)}```")
        except discord.errors.HTTPException:
            logger.error("[!] Failed to send error message - interaction token expired")


# -----------------------------
# `/reset` Command
# -----------------------------
@bot.tree.command(name="reset", description="Reset your Claude session and create a new workspace")
async def reset(interaction: discord.Interaction):
    logger.info("=" * 60)
    logger.info(f"[*] Received command: /reset")
    logger.info(f"[*] User: {interaction.user} (ID: {interaction.user.id})")
    logger.info(f"[*] Guild: {interaction.guild.name if interaction.guild else 'DM'}")
    
    await interaction.response.send_message("🫡 We're resetting our office...")
    
    try:
        user_id = interaction.user.id
        
        # Check if there is an existing session
        if user_id in user_sessions:
            old_session = user_sessions[user_id]
            old_uuid = old_session.session_uuid
            old_workspace = old_session.workspace_path
            
            # Reset session
            await old_session.reset()
            
            response_text = (
                f"[DONE] Session reset successfully!\n\n"
                f"**Old Session:**\n"
                f"  - UUID: `{old_uuid}`\n"
                f"  - Workspace: `{old_workspace}`\n\n"
                f"**New Session:**\n"
                f"  - UUID: `{old_session.session_uuid}`\n"
                f"  - Workspace: `{old_session.workspace_path}`\n\n"
                f"Your previous workspace has been preserved. You can now start fresh!"
            )
        else:
            # If no existing session, create new one
            session = await get_or_create_session(user_id)
            response_text = (
                f"[DONE] New session created!\n\n"
                f"**Session Info:**\n"
                f"  - UUID: `{session.session_uuid}`\n"
                f"  - Workspace: `{session.workspace_path}`\n"
            )
        
        await interaction.followup.send(response_text)
        logger.info(f"[+] /reset command completed successfully")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"[!] /reset command error: {str(e)}", exc_info=True)
        logger.info("=" * 60)
        await interaction.followup.send(f"[ERROR] Reset failed: {str(e)}")


# -----------------------------
# `/status` Command
# -----------------------------
@bot.tree.command(name="status", description="Check your current session information")
async def status(interaction: discord.Interaction):
    logger.info(f"[*] /status command requested by {interaction.user} (ID: {interaction.user.id})")
    
    try:
        user_id = interaction.user.id
        
        if user_id in user_sessions:
            session = user_sessions[user_id]
            
            # Calculate session age
            session_age = datetime.now() - session.created_at
            last_used_ago = datetime.now() - session.last_used
            
            # Calculate expiry
            expiry_time = session.last_used + timedelta(hours=SESSION_EXPIRY_HOURS)
            time_until_expiry = expiry_time - datetime.now()
            hours_until_expiry = time_until_expiry.total_seconds() / 3600
            
            # Check file count and size in workspace
            file_count = sum(1 for _ in session.workspace_path.rglob('*') if _.is_file())
            workspace_size_mb = session.calculate_workspace_size()
            usage_percent = (workspace_size_mb / MAX_WORKSPACE_SIZE_MB) * 100
            
            # Status indicator
            if usage_percent >= 90:
                size_status = "🔴 CRITICAL"
            elif usage_percent >= 70:
                size_status = "🟡 WARNING"
            else:
                size_status = "🟢 OK"
            
            status_text = (
                f"**Session Status**\n\n"
                f"**Session Information:**\n"
                f"  - UUID: `{session.session_uuid}`\n"
                f"  - Created: {session.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"  - Age: {session_age.total_seconds() / 60:.1f} minutes\n"
                f"  - Last Used: {last_used_ago.total_seconds():.0f} seconds ago\n"
                f"  - Expires in: {hours_until_expiry:.1f} hours\n\n"
                f"**Workspace:**\n"
                f"  - Path: `{session.workspace_path}`\n"
                f"  - Files: {file_count}\n"
                f"  - Size: {workspace_size_mb:.2f}MB / {MAX_WORKSPACE_SIZE_MB}MB ({usage_percent:.1f}%)\n"
                f"  - Status: {size_status}\n\n"
                f"**Client Status:**\n"
                f"  - Connected: {'Yes' if session.client else 'No'}\n\n"
                f"**Usage Stats:**\n"
                f"  - Total Tasks: {session.total_tasks}\n"
                f"  - Total Cost: ${session.total_cost_usd:.4f}\n\n"
                f"Use `/cleanup` to free up space or `/reset` to create a new session."
            )
        else:
            status_text = (
                f"**Session Status**\n\n"
                f"No active session found.\n"
                f"Use `/run` or `/code` to create a new session automatically."
            )
        
        await interaction.response.send_message(status_text, ephemeral=True)
    
    except Exception as e:
        logger.error(f"[!] /status command error: {str(e)}", exc_info=True)
        await interaction.response.send_message(f"[ERROR] Error checking status: {str(e)}", ephemeral=True)


# -----------------------------
# `/cleanup` Command
# -----------------------------
@bot.tree.command(name="cleanup", description="Clean up your workspace to free up space")
@app_commands.describe(delete_all="Delete entire workspace (default: keep .claude/ config)")
async def cleanup(interaction: discord.Interaction, delete_all: bool = False):
    logger.info("=" * 60)
    logger.info(f"[*] Received command: /cleanup delete_all={delete_all}")
    logger.info(f"[*] User: {interaction.user} (ID: {interaction.user.id})")
    
    await interaction.response.send_message("🧹 We're cleaning up our office...")
    
    try:
        user_id = interaction.user.id
        
        if user_id not in user_sessions:
            await interaction.followup.send(
                "[INFO] No active session found. Nothing to clean up."
            )
            logger.info("=" * 60)
            return
        
        session = user_sessions[user_id]
        
        # Calculate size before cleanup
        size_before = session.calculate_workspace_size()
        
        if delete_all:
            # Full cleanup - reset session
            await session.reset()
            size_after = 0.0
            
            response_text = (
                f"[DONE] Workspace completely cleaned!\n\n"
                f"**Cleanup Results:**\n"
                f"  - Size Before: {size_before:.2f}MB\n"
                f"  - Size After: {size_after:.2f}MB\n"
                f"  - Space Freed: {size_before:.2f}MB\n\n"
                f"**New Session:**\n"
                f"  - UUID: `{session.session_uuid}`\n"
                f"  - Workspace: `{session.workspace_path}`\n"
            )
        else:
            # Partial cleanup - keep .claude/ config
            await session.cleanup_workspace_files(keep_config=True)
            size_after = session.calculate_workspace_size()
            space_freed = size_before - size_after
            
            response_text = (
                f"[DONE] Workspace cleaned (kept .claude/ config)!\n\n"
                f"**Cleanup Results:**\n"
                f"  - Size Before: {size_before:.2f}MB\n"
                f"  - Size After: {size_after:.2f}MB\n"
                f"  - Space Freed: {space_freed:.2f}MB\n\n"
                f"Your Claude Code configuration has been preserved."
            )
        
        await interaction.followup.send(response_text)
        logger.info(f"[+] /cleanup command completed successfully (freed {size_before - size_after:.2f}MB)")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"[!] /cleanup command error: {str(e)}", exc_info=True)
        logger.info("=" * 60)
        await interaction.followup.send(f"[ERROR] Cleanup failed: {str(e)}")


# -----------------------------
# Start Bot
# -----------------------------
async def main():
    if not TOKEN:
        raise RuntimeError("Please set DISCORD_BOT_TOKEN environment variable")
    
    logger.info("[*] Starting Discord Bot...")
    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

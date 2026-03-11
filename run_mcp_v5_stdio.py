import sys
from pathlib import Path

# Add parent directory to path for package imports
parent_dir = Path(__file__).parent
sys.path.insert(0, str(parent_dir))

from src.mcp_server_v5 import mcp


if __name__ == "__main__":
    mcp.run()  # stdio transport for MCP client connections

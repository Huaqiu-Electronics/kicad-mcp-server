import os
import argparse
import logging
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from kicad_client import KiCadClient

import common_tools
import schematic_tools
import pcb_tools
from utils import get_logger, wait_for_kicad_pid, build_socket_url, wait_for_connection
from valid_editors import VALID_EDITORS

# Load environment variables
load_dotenv()

# Initialize Logger
logger = get_logger()

# Initialize MCP server
mcp = FastMCP("kicad-mcp-server")

# Global KiCad client instance (initially None)
KICAD_CLIENT: KiCadClient = None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KiCad MCP Server")
    parser.add_argument("--socket-url", help="KiCad SDK socket URL")
    parser.add_argument("--api-key", help="OpenAI API key")
    parser.add_argument("--base-url", help="OpenAI base URL")
    parser.add_argument("--model", help="OpenAI model name")    
    parser.add_argument(
        "--editor-type",
        type=str,
        choices=VALID_EDITORS,
        help="Editor type (schematic, pcb, footprint, symbol)",
    )

    args = parser.parse_args()
    
    # Set environment variables from command-line arguments if provided
    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url
    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    socket_url = None
    if args.socket_url:
        socket_url = args.socket_url
    else:
        if not args.editor_type:
            raise RuntimeError("editor-type required when socket_url not provided")

        logger.info("Waiting for KiCad process...")
        pid = wait_for_kicad_pid()

        if not pid:
            raise RuntimeError("KiCad not found")

        socket_url = build_socket_url(pid, args.editor_type)

    logger.info(f"Using socket URL: {socket_url}")

    KICAD_CLIENT = wait_for_connection(
        KiCadClient,
        socket_url,
        args.editor_type
    )

    # Initialize Context for Tool Modules
    common_tools.init_context(KICAD_CLIENT, logger)
    schematic_tools.init_context(KICAD_CLIENT, logger)
    pcb_tools.init_context(KICAD_CLIENT, logger)

    # List of tools to register
    tools_to_register = common_tools.TOOLS[:]
    
    if args.editor_type in ["schematic", "symbol"]:
        tools_to_register.extend(schematic_tools.TOOLS)
    elif args.editor_type in ["pcb", "footprint"]:
        tools_to_register.extend(pcb_tools.TOOLS)

    # Dynamically register tools
    for tool in tools_to_register:
        mcp.tool()(tool)

    # Run MCP server
    logger.info("Starting MCP server with stdio transport")
    mcp.run(transport="stdio")
    logger.info("Quitting KiCad MCP SERVER")
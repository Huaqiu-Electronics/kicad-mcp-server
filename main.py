import logging
from logging.handlers import RotatingFileHandler
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import sys
from enum import Enum
import base64
import xml.etree.ElementTree as ET
from typing_extensions import TypedDict, List
from pydantic import BaseModel
import pynng
import json

from typechat import Failure, TypeChatJsonTranslator, TypeChatValidator, create_openai_language_model

def typechat_get_llm(model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"):
    llm = create_openai_language_model(
        model= model,
        api_key=os.getenv("OPENAI_API_KEY") or "",
        endpoint=os.getenv("OPENAI_BASE_URL") or "",
    )
    llm.timeout_seconds = 60 * 3  # 3 minutes
    return llm
# Load environment variables
load_dotenv()

# Setup logging
log_dir = os.path.join(os.getcwd(), 'logs')
os.makedirs(log_dir, exist_ok=True)

# Configure logging
log_file = os.path.join(log_dir, 'kicad-mcp-server.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class KiCadCommand(str, Enum):
    """KiCad SDK commands"""
    NET_LIST = "netlist"
    PLACE_NET_LABELS = "placeNetLabels"


class API_PLACE_NETLABEL_PIN(TypedDict):
    """Pin information for net label placement"""
    designator: str
    pin_num: int


class API_PLACE_NETLABEL_PARAMS(TypedDict):
    """Parameters for placing net labels"""
    net_name: str
    pins: List[API_PLACE_NETLABEL_PIN]


class API_PLACE_NETLABELS(TypedDict):
    """Container for multiple net label placements"""
    nets: List[API_PLACE_NETLABEL_PARAMS]


class KiCadClient:
    """Client for interacting with the KiCad SDK server using NNG Request/Reply pattern"""
    
    def __init__(self, socket_url: str):
        """Initialize KiCad client with NNG socket URL"""
        logger.info(f"Initializing KiCadClient with socket URL: {socket_url}")
        self.socket_url = socket_url
        self.req_socket = pynng.Req0(recv_timeout=30000, send_timeout=30000)  # 30 seconds timeout
        self.req_socket.dial(socket_url)
        logger.info(f"Successfully connected to KiCad SDK at: {socket_url}")
    
    def get_netlist(self) -> str | None:
        """Get the complete XML representation of the current KiCad project"""
        try:
            logger.info("Sending netlist request to KiCad SDK")
            # Send netlist request
            request = {
                "cmd": KiCadCommand.NET_LIST.value
            }
            
            # Send JSON request
            self.req_socket.send(json.dumps(request).encode())
            logger.debug(f"Sent netlist request: {request}")
            
            # Receive response
            response_data = self.req_socket.recv()
            response = json.loads(response_data.decode())
            logger.debug(f"Received netlist response: {response}")
            
            netlist = response.get("net_list")
            if not netlist:
                logger.warning("No net_list found in response")
                return None

            xml_content = base64.b64decode(netlist).decode("utf-8")

            try:
                root = ET.fromstring(xml_content)
                nets_section = root.find("nets")
                if nets_section is not None:
                    root.remove(nets_section)

                # Serialize the cleaned XML back to string
                cleaned_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
                return cleaned_xml

            except ET.ParseError as e:
                logger.error(f"XML parsing failed: {e}")
                return xml_content  

        except pynng.exceptions.Timeout:
            logger.error("Timeout while calling netlist command")
            return None
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None
    
    def place_net_label(self, net_params: API_PLACE_NETLABEL_PARAMS):
        """Send a single net label placement request to the KiCad SDK server"""
        try:
            net_name = net_params['net_name']
            logger.info(f"Placing net label for: {net_name}")
            # Create request payload
            request = {
                "cmd": KiCadCommand.PLACE_NET_LABELS.value,
                "params": {
                    "action": "place_netlabels",
                    "context": net_params
                }
            }
            
            # Send JSON request
            self.req_socket.send(json.dumps(request).encode())
            logger.debug(f"Sent place net label request for {net_name}: {request}")
            
            # Receive response
            response_data = self.req_socket.recv()
            response = json.loads(response_data.decode())
            
            logger.info(f"Place net labels response: {response}")
        except pynng.exceptions.Timeout:
            logger.error(f"Timeout while placing net '{net_params['net_name']}'")
        except Exception as e:
            logger.error(f"Failed to place net '{net_params['net_name']}': {e}")
    
    def __del__(self):
        """Clean up the NNG socket"""
        try:
            self.req_socket.close()
        except:
            pass



# Initialize MCP server
mcp = FastMCP("kicad-mcp-server")


# Global KiCad client instance
KICAD_CLIENT: KiCadClient | None = None

@mcp.tool()
async def generate_net_labels(net_list: str) -> 'API_PLACE_NETLABELS | None':
    """
    Given the full XML representation of a KiCad project, and build its connections using net labels.

    Returns a list of API_PLACE_NETLABELS representing connections.
    Expected to be used with the place_all_net_labels tool to automatically place all net labels into KiCad to apply the connections.
    """

    model = typechat_get_llm()
    validator = TypeChatValidator(API_PLACE_NETLABELS)
    translator = TypeChatJsonTranslator(model, validator, API_PLACE_NETLABELS)

    instruction = f"""
You are an assistant that generates all net label connections for a KiCad project.
Return a JSON object with a single field "nets", which is a list of objects
following API_PLACE_NETLABEL_PARAMS:

API_PLACE_NETLABEL_PARAMS:
  net_name: string
  pins: list of objects with:
    - designator: string
    - pin_num: integer

Use the XML provided below to generate the net labels.
--- BEGIN NETLIST XML ---
{net_list}
--- END NETLIST XML ---
"""

    result = await translator.translate(instruction)

    if isinstance(result, Failure):
        logger.error(f"TypeChat error: {result.message}")
        return None

    return result.value


@mcp.tool()
def place_all_net_labels(nets: API_PLACE_NETLABELS):
    """
    Send multiple net label placements to the KiCad SDK HTTP server.

    Wraps each net in an AGENT_ACTION JSON payload and posts to /placeNetLabels.
    """
    if KICAD_CLIENT is None:
        logger.error("Client not initialized")
        return
    
    for net_params in nets["nets"]:
        KICAD_CLIENT.place_net_label(net_params)


@mcp.tool()
def get_current_kicad_project() -> str | None:
    """
    Get the complete xml representation of the current KiCad project using NNG.

    Returns:
        str | None: XML content of the current KiCad project.
    """
    if KICAD_CLIENT is None:
        logger.error("Client not initialized")
        return None
    
    return KICAD_CLIENT.get_netlist()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.info("Usage: python main.py <KICAD_SDK_SOCKET_URL>")
        logger.info("Example: python main.py ipc:///tmp/kicad_sdk.ipc")
        sys.exit(1)
    
    # Initialize KiCad client with the provided socket URL
    logger.info(f"Initializing KiCad client with socket URL: {sys.argv[1]}")
    KICAD_CLIENT = KiCadClient(sys.argv[1])
    
    # Run MCP server
    logger.info("Starting MCP server with stdio transport")
    mcp.run(transport="stdio")

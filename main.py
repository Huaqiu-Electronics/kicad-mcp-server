from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx
import os
from enum import Enum
import base64
import sys
from typechat import Failure, TypeChatJsonTranslator, TypeChatValidator ,create_openai_language_model
import httpx
import base64
import xml.etree.ElementTree as ET

def typechat_get_llm(model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"):
    llm = create_openai_language_model(
        model= model,
        api_key=os.getenv("OPENAI_API_KEY") or "",
        endpoint=os.getenv("OPENAI_BASE_URL") or "",
    )
    llm.timeout_seconds = 60 * 3  # 3 minutes
    return llm

load_dotenv()

from typing_extensions import TypedDict, List

class API_PLACE_NETLABEL_PIN(TypedDict):
    designator: str
    pin_num: int

class API_PLACE_NETLABEL_PARAMS(TypedDict):
    net_name: str
    pins: List[API_PLACE_NETLABEL_PIN]

class API_PLACE_NETLABELS(TypedDict):
    nets : List[API_PLACE_NETLABEL_PARAMS]


mcp = FastMCP("kicad-mcp-server")


class KiCadAPI:
    port : int | None = None

    def get_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
    
    def set_port(self, port: int):
        self.port = port


KICAD_API = KiCadAPI()

class KiCadEndPoint(str, Enum):
    """KiCad API endpoints"""

    NET_LIST = "netlist"
    PLACE_NET_LABELS = "placeNetLabels"

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
        print(f"[TypeChat Error] {result.message}")
        return None

    return result.value


@mcp.tool()
async def place_all_net_labels(nets: API_PLACE_NETLABELS):
    """
    Send multiple net label placements to the KiCad SDK HTTP server.

    Wraps each net in an AGENT_ACTION JSON payload and posts to /placeNetLabels.
    """

    async with httpx.AsyncClient() as client:
        for net_params in nets["nets"]:
            payload = {
                "action": "place_netlabels",
                "context": net_params
            }

            try:
                response = await client.post(
                    f"{KICAD_API.get_url()}/{KiCadEndPoint.PLACE_NET_LABELS.value}",
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                print("[place_all_net_labels] Response:", response.json())
            except Exception as e:
                print(f"[KiCad SDK] Failed to place net '{net_params['net_name']}': {e}")



@mcp.tool()
async def get_current_kicad_project():
    """
    Get the complete xml representation of the current KiCad project.

    Returns:
        str | None: XML content of the current KiCad project.
    """

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{KICAD_API.get_url()}/{KiCadEndPoint.NET_LIST.value}', timeout=30.0)
            response.raise_for_status()

            res = response.json()
            netlist = res.get("net_list")

            if not netlist:
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
                print(f"[KiCad SDK] XML parsing failed: {e}")
                return xml_content  

        except httpx.TimeoutException:
            print("[KiCad SDK] Timeout while calling /netlist")
            return None
        except httpx.RequestError as e:
            print(f"[KiCad SDK] Request failed: {e}")
            return None



if __name__ == "__main__":
    if len(sys.argv) < 2 :
        print("Usage: python main.py <KICAD_SDK_PORT>")
        sys.exit(1)
    
    KICAD_API.set_port(int(sys.argv[1]))
    mcp.run(transport="stdio")

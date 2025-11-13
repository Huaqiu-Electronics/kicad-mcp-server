from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx
import os
from enum import Enum
import base64
from typechat import Failure, TypeChatJsonTranslator, TypeChatValidator ,create_openai_language_model


def typechat_get_llm(model = os.getenv("OPENAI_MODEL") or "gemini-2.5-flash-nothink"):
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


mcp = FastMCP("docs")

KICAD_API_URL= "http://localhost:9234"

class KiCadEndPoint(str, Enum):
    """KiCad API endpoints"""

    NET_LIST = "netlist"
    PLACE_NET_LABELS = "placeNetLabels"

@mcp.tool()  
async def build_connections(net_list: str) -> 'API_PLACE_NETLABEL_PARAMS | None':
    """
    Given the full textual representation of a KiCad project's netlist,
    use TypeChat to extract structured pin connection information for a specific net.

    The model will parse the provided netlist context and generate a valid
    API_PLACE_NETLABEL_PARAMS structure with 'net_name' and associated 'pins'.
    """

    # Initialize TypeChat model and validator
    model = typechat_get_llm()
    validator = TypeChatValidator(API_PLACE_NETLABEL_PARAMS)
    translator = TypeChatJsonTranslator(model, validator, API_PLACE_NETLABEL_PARAMS)

    # Build a structured prompt for the model
    instruction = f"""
You are an assistant that analyzes KiCad project netlists.

The user will provide the *complete textual netlist* from a KiCad design.
Your task is to extract the structured connection data for one selected net
and represent it as JSON matching the following schema:

API_PLACE_NETLABEL_PARAMS:
  net_name: string
  pins: list of objects each with:
    - designator: string (component reference like "U1", "R3", "J1")
    - pin_num: integer (pin number connected to the net)

Guidelines:
- Carefully parse the given netlist text to determine actual net names and pin associations.
- Ignore schematic formatting, comments, and library metadata.
- Only include valid nets and connected pins.
- Ensure pin numbers are integers and designators are exact component names.

Example output:
{{
  "net_name": "VCC",
  "pins": [
    {{ "designator": "U1", "pin_num": 1 }},
    {{ "designator": "U2", "pin_num": 3 }},
    {{ "designator": "J1", "pin_num": 2 }}
  ]
}}

--- BEGIN NETLIST TEXT ---
{net_list}
--- END NETLIST TEXT ---
"""

    # Translate using TypeChat
    result = await translator.translate(instruction)

    # Handle result or failure
    if isinstance(result, Failure):
        print(f"[TypeChat Error] {result.message}")
        return None

    return result.value

import json

@mcp.tool()
async def place_net_labels(params: API_PLACE_NETLABEL_PARAMS):
    """
    Send structured net label placement data to the KiCad SDK HTTP server.

    The JSON payload follows the AGENT_ACTION schema:
    {
        "action": "place_netlabels",
        "context": <API_PLACE_NETLABEL_PARAMS>
    }

    Args:
        params (API_PLACE_NETLABEL_PARAMS): Contains the target net name
        and a list of (designator, pin_num) pairs representing where
        net labels should be placed.

    Returns:
        dict | None: KiCad server response if successful, otherwise None.
    """

    # Print params to console for debugging / visibility
    print("[place_net_labels] API_PLACE_NETLABEL_PARAMS:")
    print(json.dumps(params, indent=2))

    # Construct payload for KiCad backend (AGENT_ACTION)
    payload = {
        "action": "place_netlabels",
        "context": params
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{KICAD_API_URL}/{KiCadEndPoint.PLACE_NET_LABELS.value}",
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()

            try:
                return response.json()
            except Exception:
                # Fallback for non-JSON responses
                return {"status": "ok"}

        except httpx.TimeoutException:
            print("[KiCad SDK] Timeout while calling placeNetLabels.")
            return None
        except httpx.RequestError as e:
            print(f"[KiCad SDK] Request failed: {e}")
            return None


@mcp.tool()  
async def get_netlist():
  """
  Get the netlist of the current KiCad project
  NOTE: The netlist is also the complete representation of the schematic
  NOTE: Unless explicitly requested, while user asking for the current KiCad project , the netlist is what he wants

  Returns:
    The xml content of the netlist
  """

  async with httpx.AsyncClient() as client:
      try:
          response = await client.get(f'{KICAD_API_URL}/netlist', timeout=30.0)
          response.raise_for_status()
          res= response.json()
          netlist = res.get("net_list") or None

          if netlist:
              netlist = base64.b64decode(netlist).decode("utf-8")
              return netlist
          return None
      except httpx.TimeoutException:
          return None

if __name__ == "__main__":
    mcp.run(transport="stdio")

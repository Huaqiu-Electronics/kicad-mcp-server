from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx
import json
import os
from bs4 import BeautifulSoup
from enum import Enum
import base64
load_dotenv()

mcp = FastMCP("docs")

KICAD_API_URL= "http://localhost:9234"

class KiCadEndPoint(str, Enum):
    """KiCad API endpoints"""

    NET_LIST = "netlist"
    PLACE_NET_LABELS = "placeNetLabels"

def get_netlist():
    with httpx.Client() as client:
        try:
            response =  client.get(f'{KICAD_API_URL}/netlist', timeout=30.0)
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
    print(get_netlist())

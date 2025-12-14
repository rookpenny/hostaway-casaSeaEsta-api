from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class PMSAdapter(ABC):
    provider: str  # "hostaway", "lodgify", "guesty"

    @abstractmethod
    async def validate_credentials(self, creds: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return normalized connection info (account_id, tokens, expires_at, etc.)
        Raise exception on invalid creds.
        """

    @abstractmethod
    async def fetch_properties(self, connection: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Return list of normalized properties:
        [
          {
            "external_property_id": "...",
            "name": "...",
            "raw": {...}
          }
        ]
        """

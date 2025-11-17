from abc import ABC, abstractmethod

class BasePMSIntegration(ABC):
    def __init__(self, credentials: dict, pmc_record_id: str):
        self.credentials = credentials
        self.pmc_record_id = pmc_record_id

    @abstractmethod
    def fetch_properties(self) -> list:
        """Fetch properties from PMS API"""
        pass

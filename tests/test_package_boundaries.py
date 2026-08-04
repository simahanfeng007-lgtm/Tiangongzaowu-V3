import unittest

from communication_service import DEFAULT_PORT as COMMUNICATION_PORT
from contracts import CONTRACT_SCHEMA_VERSION
from total_gateway import DEFAULT_PORT as TOTAL_GATEWAY_PORT


class PackageBoundaryTests(unittest.TestCase):
    def test_component_ports_are_distinct(self) -> None:
        self.assertEqual(TOTAL_GATEWAY_PORT, 7184)
        self.assertEqual(COMMUNICATION_PORT, 7176)
        self.assertNotEqual(TOTAL_GATEWAY_PORT, COMMUNICATION_PORT)

    def test_contract_schema_is_explicitly_versioned(self) -> None:
        # 合同 vNext：当前写入版本 v2；v1 仅作历史行读取兼容（草案 ExecutionContractCutover）。
        self.assertEqual(CONTRACT_SCHEMA_VERSION, "tiangong.gateway.contracts.v2")
        from contracts.models import LEGACY_SCHEMA_VERSION
        self.assertEqual(LEGACY_SCHEMA_VERSION, "tiangong.gateway.contracts.v1")


if __name__ == "__main__":
    unittest.main()

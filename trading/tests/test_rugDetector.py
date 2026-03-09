"""
test_rugDetector.py — Unit tests for RugDetector module.
"""
import unittest
from trading.Services.rugDetector import RUG_METHOD_SELECTORS, TRANSFER_TOPIC


class TestRugMethodSelectors(unittest.TestCase):
    """Test the method selector constants."""

    def test_selectors_are_hex(self):
        for selector in RUG_METHOD_SELECTORS.keys():
            self.assertEqual(len(selector), 8)
            int(selector, 16)  # Should not raise

    def test_critical_selectors_present(self):
        """Key rug-pull functions should be in the selector list."""
        method_names = set(RUG_METHOD_SELECTORS.values())
        self.assertIn("removeLiquidityETH", method_names)
        self.assertIn("removeLiquidity", method_names)
        self.assertIn("pause", method_names)
        self.assertIn("blacklist", method_names)
        self.assertIn("renounceOwnership", method_names)
        self.assertIn("transferOwnership", method_names)
        self.assertIn("mint", method_names)

    def test_tax_manipulation_selectors(self):
        """Tax manipulation functions should be tracked."""
        method_names = set(RUG_METHOD_SELECTORS.values())
        tax_names = {"setFee", "setTaxRate", "updateFee", "setTax", "setSellFee", "setBuyFee", "setFees"}
        self.assertTrue(tax_names & method_names, "At least some tax functions should be tracked")

    def test_no_duplicate_selectors(self):
        """Each selector should be unique."""
        selectors = list(RUG_METHOD_SELECTORS.keys())
        self.assertEqual(len(selectors), len(set(selectors)))


class TestTransferTopic(unittest.TestCase):
    """Test the Transfer event topic constant."""

    def test_transfer_topic_format(self):
        self.assertTrue(TRANSFER_TOPIC.startswith("0x"))
        # Should be 66 chars total (0x + 64 hex chars)
        self.assertEqual(len(TRANSFER_TOPIC), 66)

    def test_transfer_topic_is_keccak(self):
        """Transfer topic should be keccak256 of the event signature."""
        from web3 import Web3
        expected = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex()
        self.assertEqual(TRANSFER_TOPIC, expected)


if __name__ == "__main__":
    unittest.main()

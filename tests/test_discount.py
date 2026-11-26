import unittest

from src import calc


class DiscountTests(unittest.TestCase):
    def test_ten_percent(self):
        self.assertEqual(calc.price_with_discount(100, 10), 90)

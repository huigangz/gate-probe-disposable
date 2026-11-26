import unittest

from src import calc


class ExistingTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)

    def test_discount_zero_pct_keeps_price(self):
        self.assertEqual(calc.price_with_discount(100, 0), 100)


    def test_add_negative(self):
        self.assertEqual(calc.add(-2, -3), -5)


if __name__ == "__main__":
    unittest.main()

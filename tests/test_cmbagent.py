import unittest
from cmbagent.cmbagent import CMBAgent

class TestCmbAgent(unittest.TestCase):
    def test_hello_cmbagent(self):
        cmbagent = CMBAgent()
        self.assertEqual(cmbagent.hello_cmbagent(), "Hello from cmbagent!")

if __name__ == '__main__':
    unittest.main()
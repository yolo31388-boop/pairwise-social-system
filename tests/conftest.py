"""pytest 辅助：测试类未继承 unittest.TestCase，这里补上断言方法。

不修改 tests/test_social.py 本身，仅在收集前为其挂载 TestCase 的断言能力。
"""

import unittest

from tests.test_social import TestSocialSystem

for _name in ("assertIsNotNone", "assertIsNone", "assertTrue", "assertFalse",
              "assertEqual", "assertNotEqual", "_formatMessage", "failureException",
              "longMessage"):
    if not hasattr(TestSocialSystem, _name):
        setattr(TestSocialSystem, _name, getattr(unittest.TestCase, _name))

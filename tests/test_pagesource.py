import unittest

from src.core.pagesource import _LRU


class PageSourceCacheTests(unittest.TestCase):
    def test_cache_hit_refreshes_recency_before_eviction(self):
        cache = _LRU(2)
        cache.put("one", 1)
        cache.put("two", 2)
        self.assertEqual(cache.get("one"), 1)
        cache.put("three", 3)
        self.assertIsNone(cache.get("two"))
        self.assertEqual(cache.get("one"), 1)
        self.assertEqual(cache.get("three"), 3)


if __name__ == "__main__":
    unittest.main()

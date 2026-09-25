"""Exercise the firmware's actual C FIFO, including overflow and wraparound."""
from collections import deque
import ctypes
from pathlib import Path
import random
import unittest

LIBRARY = Path(__file__).resolve().parents[1] / "build" / "gate_native.dll"


@unittest.skipUnless(LIBRARY.exists(), "Run scripts/build_native.ps1 first")
class EchoFifoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(LIBRARY))
        cls.lib.echo_reset.restype = None
        cls.lib.echo_write.argtypes = [ctypes.c_uint8]
        cls.lib.echo_write.restype = ctypes.c_int
        cls.lib.echo_read.restype = ctypes.c_uint8
        for name in ("echo_count", "echo_capacity"):
            getattr(cls.lib, name).restype = ctypes.c_size_t
        for name in ("echo_underflows", "echo_overflows"):
            getattr(cls.lib, name).restype = ctypes.c_uint32

    def setUp(self):
        self.lib.echo_reset()

    def test_empty_and_refill(self):
        self.assertEqual([self.lib.echo_read() for _ in range(3)], [0xFF] * 3)
        self.assertEqual(self.lib.echo_underflows(), 3)
        for value in (0, 0xFF, 0x55):
            self.assertEqual(self.lib.echo_write(value), 1)
        self.assertEqual([self.lib.echo_read() for _ in range(4)], [0, 0xFF, 0x55, 0xFF])
        self.assertEqual(self.lib.echo_underflows(), 4)

    def test_partial_reads_and_appended_writes(self):
        for value in (0x10, 0x20, 0x30):
            self.lib.echo_write(value)
        self.assertEqual(self.lib.echo_read(), 0x10)
        self.lib.echo_write(0x40)
        self.assertEqual([self.lib.echo_read() for _ in range(4)], [0x20, 0x30, 0x40, 0xFF])

    def test_full_queue_preserves_unread_data(self):
        capacity = self.lib.echo_capacity()
        for i in range(capacity):
            self.assertEqual(self.lib.echo_write(i % 251), 1)
        self.assertEqual(self.lib.echo_write(0xAA), 0)
        self.assertEqual(self.lib.echo_overflows(), 1)
        self.assertEqual(self.lib.echo_count(), capacity)
        self.assertEqual([self.lib.echo_read() for _ in range(capacity)],
                         [i % 251 for i in range(capacity)])
        self.assertEqual(self.lib.echo_read(), 0xFF)

    def test_wraparound_matches_reference(self):
        rng = random.Random(2040)
        expected = deque()
        capacity = self.lib.echo_capacity()
        underflows = overflows = 0
        for _ in range(30):
            for _ in range(rng.randrange(1, capacity * 2)):
                value = rng.randrange(256)
                if len(expected) < capacity:
                    expected.append(value)
                    self.assertEqual(self.lib.echo_write(value), 1)
                else:
                    overflows += 1
                    self.assertEqual(self.lib.echo_write(value), 0)
            for _ in range(rng.randrange(1, capacity * 2)):
                if expected:
                    value = expected.popleft()
                else:
                    value = 0xFF
                    underflows += 1
                self.assertEqual(self.lib.echo_read(), value)
            self.assertEqual(self.lib.echo_count(), len(expected))
        self.assertEqual(self.lib.echo_underflows(), underflows)
        self.assertEqual(self.lib.echo_overflows(), overflows)

    def test_reset_discards_queued_data_and_counters(self):
        self.lib.echo_read()
        self.lib.echo_write(0x42)
        self.lib.echo_reset()
        self.assertEqual(self.lib.echo_count(), 0)
        self.assertEqual(self.lib.echo_underflows(), 0)
        self.assertEqual(self.lib.echo_overflows(), 0)
        self.assertEqual(self.lib.echo_read(), 0xFF)

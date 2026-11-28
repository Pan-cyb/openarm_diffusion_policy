from threading import Event
import unittest

from desktop_policy import Session


class SessionTests(unittest.TestCase):
    def test_stop_before_start(self):
        called = Event()
        s = Session(lambda _: called.set())
        s.stop()
        s.start()
        self.assertFalse(called.is_set())
        self.assertEqual(s.state, "locked")

    def test_queued_and_inflight_predictions_cannot_overwrite_stop(self):
        queued, done = Event(), Event()
        def source(cancel):
            yield "queued prediction"
            queued.set()
            cancel.wait(2)
            try:
                yield "late prediction"
            finally:
                done.set()
        s = Session(source)
        s.start()
        self.assertTrue(queued.wait(2))
        s.stop()
        self.assertTrue(done.wait(2))
        s.update()
        self.assertEqual(s.state, "locked")
        s.start()
        self.assertEqual(s.state, "locked")

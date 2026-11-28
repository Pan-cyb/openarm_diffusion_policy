"""Local adapter for the official driver: a latched software stop must not park."""
from types import SimpleNamespace

import pyarrow as pa


class StopLatch:
    def __init__(self):
        self.latched = False

    def events(self, raw):
        for e in raw:
            if e["type"] == "INPUT":
                if e["id"] == "command":
                    command = e["value"][0].as_py()
                    if command == "soft_stop":
                        self.latched = True
                        e = {**e, "value": pa.array(["stop"])}
                    elif self.latched and command != "stop":
                        continue
                if self.latched and e["id"] == "move_position":
                    continue
            yield e

    def config_type(self, base):
        latch = self

        class StopConfig(base):
            def get_stop_config(self):
                if latch.latched:
                    return {"moves": []}
                return super().get_stop_config()

        return StopConfig


def main():
    import argparse
    import sys
    import dora_openarm.main as official

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--gravity-trial-dir")
    parser.add_argument("--gravity-percent", type=int, choices=(0,25,50,100))
    parser.add_argument("--production-gravity", action="store_true")
    parser.add_argument("--paired-teleop-state", action="store_true")
    options, remaining = parser.parse_known_args()
    trial = None
    if options.gravity_trial_dir is not None or options.gravity_percent is not None or options.production_gravity:
        if not options.gravity_trial_dir or options.gravity_percent is None:
            parser.error("incomplete gravity trial options")
        from vr_gravity import Trial
        trial = Trial(options.gravity_trial_dir, options.gravity_percent / 100.,
                      production=options.production_gravity, paired_state=options.paired_teleop_state)
    latch = StopLatch()
    raw_node = official.dora.Node
    driver = official.openarm_driver

    class Node:
        def __init__(self):
            self.raw = raw_node()

        def __iter__(self):
            return latch.events(trial.events(self.raw) if trial else self.raw)

        def send_output(self, *args, **kwargs):
            if trial and args[0] == "status":
                if trial.retry_stop_pending and args[1][0].as_py() == "started":
                    return
                trial.status(args[1][0].as_py())
            return self.raw.send_output(*args, **kwargs)

    official.dora = SimpleNamespace(Node=Node)
    official.openarm_driver = SimpleNamespace(
        Config=latch.config_type(driver.Config),
        SingleArmDriver=trial.driver_type(driver.SingleArmDriver) if trial else driver.SingleArmDriver)
    argv = sys.argv
    try:
        sys.argv = [argv[0], *remaining]
        official.main()
    except Exception:
        if trial:
            trial.fault.touch(exist_ok=True)
        raise
    finally:
        sys.argv = argv
        if trial and trial.owner is not None:
            trial.abort("gravity worker exited with active driver")


if __name__ == "__main__":
    main()

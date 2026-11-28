"""Versioned V2 pedestal hand-delta start, in official driver joint order/radians."""

from copy import deepcopy
from importlib.resources import files
import math
import shlex

import yaml

PROFILE = "wide-40mm-direct-v2"
# Mirrored 7R solution for +/-40 mm along arm_origin Y; J8 stays as supplied.
_RIGHT = (0.00414194, 0.09215102, 0.09043025, 1.55396008,
          0.09139800, -0.00433028, 0.09158906)


def positions():
    return {"right_arm": [*_RIGHT, -0.4],
            "left_arm": [*(v if i == 3 else -v for i, v in enumerate(_RIGHT)), 0.4]}


def driver_config():
    source = files("openarm_driver").joinpath("configs/openarm_pedestal.yaml")
    cfg = yaml.safe_load(source.read_text())
    moves = cfg["start"]["moves"]
    wide = positions()
    for side, target in wide.items():
        expected = [0., 0., 0., math.pi / 2, 0., 0., 0., target[-1]]
        if len(moves) != 1 or moves[0]["position"][side] != expected:
            raise ValueError("Unexpected pedestal startup baseline; revalidate wide pose")
    move = deepcopy(moves[0])
    move.update(name=PROFILE, position=wide, hz=50, duration=4)
    moves[:] = [move]
    return cfg


def use_driver_config(raw, path):
    module = yaml.safe_load(raw)
    arms = [n for n in module["nodes"] if n["id"] in ("left", "right")]
    if sorted(n["id"] for n in arms) != ["left", "right"]:
        raise ValueError("Missing bimanual driver")
    for arm in arms:
        args = shlex.split(arm["args"])
        i = args.index("--config") + 1
        if args[i] != "openarm_pedestal":
            raise ValueError("Expected original pedestal driver config")
        args[i] = str(path)
        arm["args"] = shlex.join(args)
    return yaml.safe_dump(module, sort_keys=False).encode()

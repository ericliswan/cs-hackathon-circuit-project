import sys

wire_factory_dir = "/Users/eric/Development/cs-hackathon-circuit-project/addon/wires"
if wire_factory_dir not in sys.path:
    sys.path.append(wire_factory_dir)

import wire_factory
import importlib
importlib.reload(wire_factory)  # so edits saved in VSCode take effect on rerun

w = wire_factory.create_wire("test_wire", (0, 0, 0), (0.5, 0, 0))
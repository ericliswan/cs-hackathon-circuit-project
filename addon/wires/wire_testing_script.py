import sys
wire_factory_dir = "/Users/eric/Development/cs-hackathon-circuit-project/addon/wires"
if wire_factory_dir not in sys.path:
    sys.path.append(wire_factory_dir)
import wire_factory
import importlib
importlib.reload(wire_factory)

w = wire_factory.create_wire((0, 0, 0), (3, 2, 0), name="test_wire")
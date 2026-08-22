from AC_Circuit_Solver import *
import uuid
from collections import defaultdict, deque
from typing import Union

Hole = Union[str, tuple, int]
Row  = list

"""
Components get parsed in the following way from the breadboard:

components = [
    ["Wire", 'VCC', (1,"b")],
    ["Wire", (1,"a"), (2,"c")],
    ["Resistor", (2,"c"), (4,"c"), 500)],
    ["Wire", (1,"a"), (2,"c")],
    ["Resistor", (5,"c"), (7,"c"), 1000],
    ["Wire", (7,"b"), 'GND']
]
VCC represent positive power supply, and GND represents the GND.
"""

# phasor stores the magnitude and the phase of the voltage source
phasor = (12, 30)

CLASS_NAME = {
    "RESISTOR1": (Resistor, 250),
    "RESISTOR2": (Resistor, 500),
    "RESISTOR3": (Resistor, 1000),
    "CAPACITOR1": (Capacitor, 0.47e-6),
    "CAPACITOR2": (Capacitor, 47e-6),
    "CAPACITOR3": (Capacitor, 470e-6),
    "INDUCTOR1": (Inductor, 2.2e-6),
    "INDUCTOR2": (Inductor, 220e-6),
    "INDUCTOR3": (Inductor, 220e-3),
    "VOLTAGESOURCE": (VoltageSource, phasor)
}

NODES = {
    "VCC": 1,
    "GND": 0
}

def node(coord: Hole) -> int:
    if coord in NODES: 
        return NODES[coord]
    if isinstance(coord, (tuple, list)): 
        return int(coord[0]) + 1
    if isinstance(coord, int): 
        return coord
    return int("".join(ch for ch in str(coord) if ch.isdigit())) + 1

def check_complete(components: list[Row]) -> bool:
    """Checks whether the circuit is complete, and that there is a complete path
    from VCC to GND."""
    adj = defaultdict(set)
    for row in components:
        if row[0] == "PROBE":
            continue
        a, b = node(row[1]), node(row[2])
        adj[a].add(b); adj[b].add(a)

    seen, dq = {0}, deque([0])
    while dq:
        for nb in adj[dq.popleft()]:
            if nb not in seen: seen.add(nb); dq.append(nb)
    return 1 in seen

def compact_components(components: list[list[str]]) -> list[list[str]]:
    ys = [[row[0], node(row[1]), node(row[2])] for row in components]

    for wire in ys:
        if wire[0] != "WIRE":
            continue
        a, b = wire[1], wire[2]
        if a == b:
            continue
        if a in (0, 1) and b in (0, 1):
            continue          # wire ties VCC straight to GND: leave it
        keep, drop = (a, b) if (a in (0, 1) or a < b) else (b, a)
        for row in ys:        # rewrite EVERYWHERE
            if row[1] == drop: row[1] = keep
            if row[2] == drop: row[2] = keep
 
    # drop the wires and any component shorted onto a single node
    return [r for r in ys if r[0] != "WIRE" and r[1] != r[2]]  

def parse_raw_components(components: list[list[str]]) -> tuple[list[Component], tuple[int, int] | None]:
    """Parses raw components with a name, and two nodes that are coded in
    two dimensions (they have an x and y coordinate). The x coordinate is an
    integer from 1 to 120 and the y coordinate is a letter from a to e.

    Reducation is also performed so any two coordinate node tuple is compressed
    into a single number. This is valid since along any rail, the nodes with
    letters a,b,c,d,e are all connected. Hence anything on the same rail can be
    treated as connected at the same node.
    """
    reduced_components = []
    tup_nodes: tuple[int, int] | None = None

    components_merged = compact_components(components)
    for comp in components_merged:
        if len(comp) == 3:
            name, node_a, node_b = comp
            class_name, value = CLASS_NAME.get(name, (None, None))
            unique_id = str(uuid.uuid4())
            # Increasing every node by 1 to account for VCC being taken
            # as node 1
            comp_reduced = None
            # Active component

            if name == "PROBE":
                tup_nodes = (int(node_a), int(node_b))
            else:
                comp_reduced = class_name(unique_id, node_a, node_b, value)
                reduced_components.append(comp_reduced)
    return (reduced_components, tup_nodes)

test = [["WIRE", 'VCC', (1,"b")],
    ["WIRE", (1,"a"), (2,"c")],
    ["RESISTOR1", (2,"c"), (4,"c")],
    ["WIRE", (4,"b"), (5,"d")],
    ["RESISTOR2", (5,"c"), (7,"c")],
    ["CAPACITOR2", (5,"c"), (7,"c")],
    ["WIRE", (7,"b"), 'GND'],
    ["PROBE", (5,"c"), (7,"c")]]

# Inputs that the user sets on the oscilloscope
mag_input, phase_input, frequency = 12, 0, 50

#Calculating omega based on the frequency
FIFTY_HZ = 2*np.pi*frequency
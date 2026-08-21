import numpy as np
import json
import cmath
import matplotlib.pyplot as plt

class Component:
    """
    Class representing an abstract component in a circuit, connected to nodes
    in a circuit. Can be a passive component or sources.
    """

    def __init__(self, name: str, nodeA: int, nodeB: int):
        """
        Initializes a component with a name and two nodes.

        Parameters:
            name: the name of the component, labelled by the user
            nodeA: the node connected to the positive terminal of the component,
            if any
            nodeB: the node connected to the negative terminal of the componenet,
            if any

        Representation Invariants:
            - nodeA and nodeB must be non-negative integers
        """

        if not (isinstance(nodeA, int) and isinstance(nodeB, int)):
            raise TypeError("nodeA and nodeB must be integers")
        if nodeA < 0 or nodeB < 0:
            raise ValueError("nodeA and nodeB must be non-negative")
        if nodeA == nodeB:
            raise ValueError(f"{name}: both terminals on node {nodeA}")

        self._name = name
        self._nodeA = nodeA
        self._nodeB = nodeB

    def get_name(self) -> str:
        """Returns the name of the component"""

        return self._name
    
    def get_nodes(self) -> tuple[int, int]:
        """
        Returns the nodes connected to the component. A is on the left 
        (first element), B is on the right (second element).
        """

        return (self._nodeA, self._nodeB)

    def get_value(self) -> float:
        """Returns the value of the component"""
        raise NotImplementedError("Subclasses must implement get_value()")

class ActiveComponent(Component):
    def __init__(self, name: str, nodeA: int, nodeB: int, value: float, phase: float = 0.0):
        super().__init__(name, nodeA, nodeB)
        self._value = value
        self._phase = phase

    def get_value(self) -> float:
        """
        Returns the value of the active component, which could be voltage
        or current            
        """
        return self._value

    def get_phase(self) -> float:
        """
        Returns the phase of the active component, an angle in degrees.
        """
        return self._phase

    def get_phasor(self) -> complex:
        """
        Source value as a complex phasor. Phase in degrees.
        """
        return self._value * np.exp(1j * np.radians(self.get_phase()))

    @property
    def phase(self) -> float:
        """Return phase of source, normalised to the principal angle"""
        return (self._phase + 180) % 360 - 180
    
class PassiveComponent(Component):
    """
    Subclass of Component, representing a passive component in a circuit, 
    which encompasses resistors, capacitors, and inductors. 
    """
    def __init__(self, name: str, nodeA: int, nodeB: int, value: float, current: float | None = None):
        """
        Initializes a passive component with a name and two nodes. 
        The component is connected between two nodes in the circuit, node A is
        the positive terminal and node B is the negative terminal.
        """
        super().__init__(name, nodeA, nodeB)
        self._value = value
        self._current = current

    def get_value(self) -> float:
        """
        Returns the value of the passive component, which could be resistance,
        capacitance or inductance.
        """
        return self._value

    # Access current value, use self.current
    @property
    def current(self) -> float | None:           
        """Return the current value"""
        return self._current
    
    # Set current value, use self.current = value
    @current.setter
    def current(self, val: float | None) -> None:
        self._current = val
    
    # To be overriden in the child class
    def get_impedance(self, omega: float = 0.0) -> float:
        """
        Returns the impedance of the component.

        Parameters:
            omega: Angular frequency (radians/sec). Defaults to 0 in DC circuits.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must define get_impedance()"
        )

    def __repr__(self) -> str:
        return f"{self.get_name()}, {self.get_nodes()[0]}->{self.get_nodes()[0]}, {self.get_value()}"

    def admittance(self, omega: float = 0.0) -> complex:
        z = self.get_impedance(omega)
        if z == 0:
            raise ValueError(f"{self.get_name()}: zero impedance (short circuit)")
        return 1 / z

    def v_drop(self, node_voltages: dict[int, float] | None = None) -> float:
        """
        Returns the voltage drop across the component.

        If nodal voltages are known, computes V = V_nodeA - V_nodeB.
        Otherwise, fall back to Ohm's law: V = I * Z (requires self.current to be known).

        Preconditions:
            The node voltages dictionary and current are not empty
        """
        nodeA, nodeB = self.get_nodes()

        if node_voltages is not None and nodeA in node_voltages and nodeB in node_voltages:
            return node_voltages[nodeA] - node_voltages[nodeB]

        if self.current is not None:
            return self.current * self.get_impedance()

        return 0.0 #fallback 

    def calc_current(self, node_voltages: dict[int, float]) -> float:
        """Returns the current flowing through the component given the node
        voltages.

        Parameters:
            node_voltages (list[float]): A list of all the node voltages, where
            the value of index i represents the voltage at node i.
        
        Preconditions:
            The node voltages at node A and node B are not empty.
            The node_voltages dictionary is not empty
        """

        return self.v_drop(node_voltages)/self.get_impedance()
        
class Resistor(PassiveComponent):
    """
    Concrete subclass of PassiveComponent, representing a resistor. 
    """
    def __init__(self, name: str, nodeA: int, nodeB: int, resistance: float):
        """
        Initializes a resistor with a name, two nodes, with a resistance value.
        """
        super().__init__(name, nodeA, nodeB, value=resistance, current=None)

    # Access resistance value, use self.resistance
    @property
    def R(self) -> float:
        """Return the resistance value."""
        return self._value

    # Set resistance value, use self.resistance = value
    @R.setter
    def resistance(self, val: float) -> None:
        if val <= 0:
            raise ValueError("Resistance must be positive")
        self._value = val
        self.current = None

    def get_impedance(self, omega: float = 0.0) -> complex:
        return complex(self.R, 0.0)

class Capacitor(PassiveComponent):
    """
    Concrete subclass of PassiveComponent, representing a capacitor.
    """
    def __init__(self, name: str, nodeA: int, nodeB: int, capacitance: float):
        """
        Initialises a Capacitor with a name, two nodes and a capacitance (Farads).

        Preconditions:
            - the capacitance is positive
        """
        super().__init__(name, nodeA, nodeB, value=capacitance)

    @property
    def C(self) -> float:
        """
        Return the capacitance value.
        """
        return self.get_value()

    @C.setter
    def capacitance(self, val: float) -> None:
        """
        Set a capacitance value.
        """
        if val <= 0:
            raise ValueError("capacitance must be positive")
        self._value = val

    def impedance(self, omega: float = 0.0) -> complex:
        """Returns the impedance of a capacitor: Z = 1/jwC. 
        In DC circuits, it is an open circuit. 
        """
        if omega == 0:
            return complex(float("inf"), 0.0)
        return 1 / (1j * omega * self.C)

class Inductor(PassiveComponent):
    """Concrete subclass of Passive Component, representing an inductor."""
    def __init__(self, name: str, nodeA: int, nodeB: int, inductance: float):
        """
        Initialises an instance of an inductor with an inductance (Henries).
        
        Preconditions:
            inductance value is positive
        """
        super().__init__(name, nodeA, nodeB, value=inductance)

    @property
    def L(self) -> float:
        """
        Returns the inductance.
        """
        return self.get_value()

    @L.setter
    def L(self, val: float) -> None:
        """
        Set an inductance.
        """
        if val <= 0:
            raise ValueError("inductance must be positive.")
        self._value = val

    def get_impedance(self, omega: float = 0) -> complex:
        """
        Returns the impedance of the inductor, Z = jwL.
        """
        return 1j * omega * self.L

class VoltageSource(ActiveComponent):
    def __init__(self, name: str, nodeA: int, nodeB: int, voltage: float, phase: float = 0.0):
        super().__init__(name, nodeA, nodeB, value=voltage, phase=phase)

    def get_voltage(self) -> float:
        return self._value

class CurrentSource(ActiveComponent):
    def __init__(self, name: str, nodeA: int, nodeB: int, current: float, phase: float = 0.0):
        super().__init__(name, nodeA, nodeB, value=current, phase=phase)

    def get_current(self) -> float:
        return self._value

# Tolerance for checking impedance values if they are small, which will inflate
# current values
SHORT_TOL = 1e-12

def solve_nodal(components: list[Component], omega: float = 0.0) -> tuple[list[complex], dict[str, complex]]:

    all_nodes = {n for c in components for n in c.get_nodes()} # set of of all nodes

    # Containing all nodes except the ground (0)
    nodal_list = sorted(all_nodes - {0})

    if not nodal_list:
        raise ValueError("circuit has no non-ground nodes")

    # Containing all voltage sources and short-circuit passive components (only applicable to DC)
    branch_list = []

    for comp in components:
        if isinstance(comp, VoltageSource):
            branch_list.append(comp)
        # If omega is negligible for an active component, aka DC circuit ignore
        elif isinstance(comp, PassiveComponent) and abs(comp.get_impedance(omega)) < SHORT_TOL:
            branch_list.append(comp)

    # Keyed by id so the solver does not require component to be hashable
    dt_nodes = {node: i for i, node in enumerate(nodal_list)}
    dt_branch = {id(c): i + len(nodal_list) for i, c in enumerate(branch_list)}
    size = len(nodal_list) + len(branch_list)

    # Coefficients for sources/source currents V1, V2, ..., IVS1, IVS2, ...
    # Matrix rows for nodes, N1, N2, N3, ..., VS1, VS2...
    # Complex mode used to support complex numbers (phasors)
    coefficients = np.zeros((size, size), dtype=complex)

    # Value after KCL or voltage drop across two nodes
    result = np.zeros(size, dtype=complex)

    for comp in components:
        # Retrieving index of node to modify values in matrix
        nodeA, nodeB = comp.get_nodes()
        # Retrieving the left/right nodes of the components
        A = None if nodeA == 0 else dt_nodes[nodeA]
        B = None if nodeB == 0 else dt_nodes[nodeB]
        # Index of component in dt_branch
        branch_index = dt_branch.get(id(comp))

        if branch_index is not None:
            e = comp.get_phasor() if isinstance(comp, ActiveComponent) else 0j
            result[branch_index] += e

            #If both nodes are not zero, then V_A - V_B = V
            if A is not None:
                coefficients[branch_index][A] = 1 # +V_A
                coefficients[A][branch_index] = 1 # Current entering node A

            if nodeB != 0:
                coefficients[branch_index][B] = -1 #-V_B
                coefficients[B][branch_index] = -1 # Current leaving node B

        # Handling matrix modification for passive components
        elif isinstance(comp, PassiveComponent):
            # Using conductance/admittance
            Y = comp.admittance(omega)
            # Case where two nodes are not the ground
            if nodeA != 0 and nodeB != 0:
                # Current leaving node is positive
                coefficients[A][A] += Y
                coefficients[B][B] += Y
                coefficients[A][B] -= Y
                coefficients[B][A] -= Y
            # When one of the nodes is the ground, only one value is added to
            # the matrix
            if nodeA != 0 and nodeB == 0:
                coefficients[A][A] += Y
            if nodeB != 0 and nodeA == 0:
                coefficients[B][B] += Y

        # Handling matrix modification for current source
        elif isinstance(comp, CurrentSource):
            I = comp.get_current()

            # Ensure the current value is defined
            if I is None:
                raise ValueError(f"current source {comp.get_name()} has no value")

            # Modifying the result to account for KCL
            # Note that the signs are reversed since by KCL, I is on the LHS
            # but it gets carried over to the RHS
            if A is not None:
                result[A] += -float(I)
            if B is not None:
                result[B] += float(I)
 
    #Solving for nodal voltages by using inverse matrix
    try:
        x = np.linalg.solve(coefficients, result)
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "singular matrix"
        ) from exc

    # List of all the nodal voltages: V0, V1, V2, ...
    # Unused node numbers stay NaN so they can't be mistaken for 0 V.
    v_nodes = [complex("nan+nanj")] * (max(nodal_list) + 1)
    v_nodes[0] = 0j
    for i, node in enumerate(nodal_list):
        v_nodes[node] = complex(x[i])
 
    i_branches = {c.get_name(): complex(x[dt_branch[id(c)]]) for c in branch_list}
 
    return v_nodes, i_branches

def format_json(component_list: list[Component]) -> str:
    """Returns the component list in JSON format"""
    comp_dict = dict()
    for component in component_list:
        A, B = component.get_nodes()
        comp_dict[component.get_name()] = {"Type": type(component).__name__,"Nodes": (A,B), "Value": component.get_value()}

    return json.dumps(comp_dict, indent=4)

def find_output_voltage(node_pair: tuple[int, int], nodal_voltages: list[complex], omega: float) -> tuple[float, float, float]:
    """Returns the output voltage parameters, representing the voltage drop across two nodes.

    By default, the first node in the list is the positive terminal and the second
    is the negative terminal, which will be used in a certain channel in the
    oscilloscope.

    The tuple being returned is (v_mag, omega, phi) where v_mag is the magnitude of the
    voltage phasor and phi is the phase. RMS values are not used.

    These parameters will be used to plot the graph: 
        V(t) = Re(V_s*exp(j(wt + phi)))
        V(t) = V_s * cos(wt + phi)
    """
    a, b = node_pair # Retrieve node indices
    try:
        mag, phi = cmath.polar(nodal_voltages[a] - nodal_voltages[b])
        return (mag, omega, phi)
    except:
        return (0.0, 0.0, 0.0)

def plot_voltage(mag_s, omega_s, phi_s, mag_o, omega_o, phi_o, x_scale_mult=1, y_scale_mult=1) -> None:
    """
    Prepares the graph of the sinusoidal voltage wave form. Variables with
    subscript s denote parameters of the source voltage while variables with 
    subscript o denote parameters of the output voltage.
    
    """
    plt.figure(figsize=(8,4))
    # Default plotting dimensions include 2 wave cycles, one before the origin,
    # and one after as period = (2*pi)/omega
    x_min_s = -(2* np.pi)/omega_s
    x_max_s = (2* np.pi)/omega_s

    # Slightly increasing the min/max height by default for extra padding on the
    # top and bottom
    y_min_s = mag_s * -1.1
    y_max_s = mag_s * 1.1

    t_s = np.linspace(x_min_s*x_scale_mult, x_max_s*x_scale_mult, 400)
    # Plotting the voltage wave form
    V_s = mag_s * np.cos(omega_s*t_s + phi_s)
    # Plot time in milliseconds
    plt.plot(t_s*1000, V_s, linewidth=3.5, color='blue')

    # Same procedure with the output voltage
    x_min_o = -(2* np.pi)/omega_o
    x_max_o = (2* np.pi)/omega_o

    # Slightly increasing the min/max height by default for extra padding on the
    # top and bottom
    y_min_o = mag_o * -1.1
    y_max_o = mag_o * 1.1

    t_o = np.linspace(x_min_o*x_scale_mult, x_max_o*x_scale_mult, 400)
    # Plotting the voltage wave form
    V_o= mag_o * np.cos(omega_o*t_o + phi_o)
    # Plot time in milliseconds
    
    plt.plot(t_o*1000, V_o, linewidth=3.5, color='crimson')    
    
    # Applying boundaries
    plt.xlim(min(x_min_s, x_min_o)*x_scale_mult*1000, max(x_max_s, x_max_o)*x_scale_mult*1000)
    plt.ylim(min(y_min_s, y_min_o)*y_scale_mult, max(y_max_s, y_max_o)*y_scale_mult)

    # major and minor grid lines
    plt.minorticks_on()
    plt.grid(which='major', color='black', linestyle='-', linewidth=0.5)
    plt.grid(which='minor', color='gray', linestyle='-', linewidth=0.25)

    plt.show()

if __name__ == "__main__":

    # Some test components
    R1 = Resistor('R1', 1, 2, 890)
    R2 = Resistor('R2', 2, 0, 3300)
    R3 = Resistor('R3', 2, 3, 1100)
    R4 = Resistor('R4', 3, 0, 5300)
    VS1 = VoltageSource('VS1', 1, 0, 12, phase=30)
    CS1 = CurrentSource('CS1', 3, 1, 0.008)

    components = [R1, R2, R3, VS1, CS1, R4]

    print(format_json(components))

    FIFTY_HZ = 2*np.pi*50

    v_nodes, i_sources = solve_nodal(components, omega=FIFTY_HZ)
    print([complex(round(x.real, 4), round(x.imag, 4)) for x in v_nodes])
    params = find_output_voltage((2,0), v_nodes, omega=FIFTY_HZ)
    plot_voltage(*params, VS1.get_value(), FIFTY_HZ, VS1.get_phase())


    
# Cs UQCS Hackathon Project - 3D Circuit Modeller and Tester

<img width="1600" height="1000" alt="sine_wave" src="https://github.com/user-attachments/assets/8bf36d8b-bf46-4309-bba4-6cbbb3f260c6" />

### Idea

This circuit project provides users with basic equipment to test AC voltage inputs in circuits with components like Resistors, Capacitors, and Inductors. An oscilloscope with an AC waveform generator is rendered in 3D in Blender, along with a breadboard. The breadboard allows users to drag and drop components like jumper wires and other components, wire them up and see the voltage waveform output. The purpose is to test and experiment with different voltage inputs and probe measurements, which would be invaluable to students wanting to catch up on a practical or to gain more skills with using oscilloscopes and building circuits. 3D rendering of the equipment makes the construction more realistic compared to websites like Tinkercad.

### The Stack

Python is used for all the backend operations like solving the circuit, parsing and formatting; it also supports the logic for physically changing the circuit, which refines the parameters that affect the circuit, feeding those new values into the circuit solver and then updating it onto the oscilloscope display.

Blender is used to render the components and equipment in 3D, supporting interactive elements like adding components to the breadboard and tuning the parameters for the input voltage waveform.

### Parsing the Breadboard Nodes

The Breadboard consists of pins arranged in rows and columns, where pins in the same row are connected. The coordinates of the component nodes are determined based on the relative location, and then, via a hash map, the coordinates are determined. Two tuples are outputted which follow the form (x, a) and (y, b) for each of the nodes, where x and y are integers and a and b are single letters.

A list of all the components and their nodes is updated when the user drags them onto the breadboard. These include wires, resistors, capacitors and inductors and two nodes (connection points to the breadboard). To format and validate, a function receives the input list and performs a Depth First Search (DFS) to determine whether the circuit is valid. If so, the list of components gets reduced in a way that wires get removed, the nodes merge, and a transformation is applied to flatten the 2D coordinates into a single integer for each node. Using dictionary lookups, the list of components is formatted in a way that has a list of objects with unique identifiers.

Raw output from Breadboard coordinates (2D node coordinates, includes jumper wires, and various types to represent the node from 'VCC', 'GND', 1, 'b'). VCC is taken as the positive power supply, and GND is the ground.

```[["WIRE", 'VCC', (1,"b")], ["WIRE", (1,"a"), (2,"c")], ["RESISTOR1", (2,"c"), (4,"c")], ["WIRE", (4,"b"), (5,"d")], ["RESISTOR2", (5,"c"), (7,"c")], ["CAPACITOR2", (5,"c"), (7,"c")], ["WIRE", (7,"b"), 'GND'], ["PROBE", (5,"c"), (7,"c")]]```

Structured output (All wires removed, nodes merged with components, and the voltage source object appended). Note how all nodes are normalised to integers. The following output is a __repr__ representation of the list where the notation 1->5 represents the component being connected to node 1 and node 5. Unique identifiers are used to give the components different names.

```[67b9ffcd-403b-47f4-89f5-6f7d111a4c33, 1->5, 250, 31fa8cf3-1b27-4358-bf2a-5feb930ac699, 5->0, 500, bedd60a2-4496-4960-adf0-6502e99ec46e, 5->0, 4.7e-05, <AC_Circuit_Solver.VoltageSource object at 0x104458d70>]```

### AC Circuit Solver

The solver utilises Modified Nodal Analysis (MNA) to solve both AC/DC circuits, allowing for independent voltage sources and current sources to be factored into analysis. This involves making a matrix with the following unknowns (nodal voltages, N_x; voltage source, V_S; currents referring to the voltage source. The ground node is omitted from the matrix as it creates an unnecessary equation, since the rule of thumb is that for n nodes, n-1 equations are needed, as the ground is taken to be at a voltage of 0 V.

<img width="857" height="418" alt="Screenshot 2026-08-22 at 4 16 18 pm" src="https://github.com/user-attachments/assets/1b515a61-556d-42d5-b8d0-51e21e71154b" />

### Conventions

We used the following conventions and assumed currents leave Node A (the left-most node when instantiated as an object). The order does not matter for the nodal analysis solver; if the direction is reversed, the sign compensates.

<img width="678" height="451" alt="Screenshot 2026-08-22 at 6 17 03 pm" src="https://github.com/user-attachments/assets/ce2ce3d1-4d7e-4653-a7d2-2ed2dca0dc41" />

### UML Diagram

<img width="992" height="550" alt="Circuit Analysis UML - Hackathon drawio" src="https://github.com/user-attachments/assets/d7f1c837-f41c-44ec-ae62-00801feb2212" />

### Displaying the voltage waveforms on the oscilloscope

During testing, Python matplotlib was used to plot the waveforms based on three conditions: magnitude, omega and phase, which uniquely determine a sinusoidal function. The magnitude, frequency and phase shift of the input voltage wave can be adjusted by the user, which gets instantaneously updated on the oscilloscope display. If the circuit is valid, both voltage waveforms get plotted on the same graph. An example graph shows the matplotlib representation of the voltage output and input waveforms after the circuit solver finds the three parameters. The x-axis is time in milliseconds (ms), and the y-axis is the voltage (V).

<img width="850" height="425" alt="Example Voltage Plot" src="https://github.com/user-attachments/assets/980a035d-1bcb-4cc3-8643-4d86cf9e4295" />

### UI Breakdown - Blender

##### Toggle Component Selection

The components can be selected by the user so that when they select nodes on the breadboard, a component of the user's choice is added. If no component is toggled, then no actions will be taken. The variable containing the component name is passed to the breadboard script via the context bpy scene.

<img width="405" height="429" alt="Toggle select" src="https://github.com/user-attachments/assets/32852ce4-06a1-4328-b114-af9fead2e0cb" />

##### Breadboard Node Selection

As the user hovers over a node, a circle appears to confirm selection, which is done through raycasting. 

<img width="707" height="693" alt="Breadboard selection" src="https://github.com/user-attachments/assets/e634da0a-ccbd-4614-8f9b-84a67d0d87d7" />





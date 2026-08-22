#Cs UQCS Hackathon Project

### Idea

This circuit project provides users with basic equipment to test AC voltage inputs in circuits with components like Resistors, Capacitors, and Inductors. An oscilloscope with an AC waveform generator is rendered in Blender, along with a breadboard. The breadboard allows users to drag and drop components like jumper wires and other components, wire them up and see the voltage waveform output. The purpose is to test and experiment with different voltage inputs and probe measurements, which would be invaluable to students wanting to catch up on a practical or to gain more skills with using oscilloscopes and building circuits. 3D rendering of the equipment makes the construction more realistic compared to websites like Tinkercad.

### The Stack

Python is used for all the backend operations like solving the circuit, parsing and formatting; it also supports the logic for physically changing the circuit, which refines the parameters that affect the circuit, feeding those new values into the circuit solver and then updating it onto the oscilloscope display.

Blender is used to render the components and equipment in 3D.

### Parsing the Breadboard Nodes

The Breadboard consists of pins arranged in rows and columns, where pins in the same row are connected. The coordinates of the component nodes are determined based on the relative location, and then, via a hash map, the coordinates are determined. Two tuples are outputted which follow the form (x, a) and (y, b) for each of the nodes, where x and y are integers and a and b are single letters.

A list of all the components and their nodes is updated when the user drags them onto the breadboard. These include wires, resistors, capacitors and inductors and two nodes (connection points to the breadboard). To format and validate, a function receives the input list and performs a Depth First Search (DFS) to determine whether the circuit is valid. If so, the list of components gets reduced in a way that wires get removed, the nodes merge, and a transformation is applied to flatten the 2D coordinates into a single integer for each node. Using dictionary lookups, the list of components is formatted in a way that has a list of objects with unique identifiers.

### AC Circuit Solver

The solver utilises Modified Nodal Analysis (MNA) to solve both AC/DC circuits, allowing for independent voltage sources and current sources to be factored into analysis. This involves making a matrix with the following unknowns (nodal voltages, N_x; voltage source, V_S; currents referring to the voltage source. The ground node is omitted from the matrix as it creates an unnecessary equation, since the rule of thumb is that for n nodes, n-1 equations are needed, as the ground is taken to be at a voltage of 0 V.

<img width="857" height="418" alt="Screenshot 2026-08-22 at 4 16 18 pm" src="https://github.com/user-attachments/assets/1b515a61-556d-42d5-b8d0-51e21e71154b" />

### Conventions

The following conventions were taken, and currents were assumed to leave Node A (the left-most node when instantiated as an object). The order does not matter for the nodal analysis solver, as if the direction was reversed then the sign would compensate.

<img width="203" height="168" alt="Conventions + extra drawio" src="https://github.com/user-attachments/assets/85d89ebd-dbf1-4d5e-826c-b16397e0632b" />

### UML Diagram

<img width="992" height="550" alt="Circuit Analysis UML - Hackathon drawio" src="https://github.com/user-attachments/assets/d7f1c837-f41c-44ec-ae62-00801feb2212" />

### Displaying the voltage waveforms on the oscilloscope

During testing, Python matplotlib was used to plot the waveforms based on three conditions: magnitude, omega and phase, which uniquely determine a sinusoidal function. The magnitude, frequency and phase shift of the input voltage wave can be adjusted by the user, which gets instantaneously updated on the oscilloscope display.



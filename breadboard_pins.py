import bpy
import math
import numpy as np
from mathutils import Vector 

def get_pin_dict(grid_origin_name : str) -> dict[str, tuple[float, float, float]]: 
    ''' 
    Returns dictionary in format {pin_key : local coordinate}, 
        pin_key (string): eg, a_1
        local coordinate (tuple): (x, y, z)
    local coordinates is relative to grid_origin
    '''
    grid_origin = bpy.data.objects.get(grid_origin_name)
    if not grid_origin: 
        print(f"Error: Object '{grid_origin_name}' not found.")
        return {}

    # Local offsets between pins
    X_DISTANCE_PINS = 0.00254          # 0.1" standard pin pitch
    X_DISTANCE_MIDDLE_PINS = 0.00762   # 0.3" standard center channel
    Y_DISTANCE_PINS = 0.00254          # 0.1" standard pin pitch (square grid)

    LETTERS = ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j')
    pins = {}

    for num in range(1, 64):
        # Calculate row Y offset (0-indexed shift from row 1)
        y_offset = (num - 1) * -Y_DISTANCE_PINS  
        x_offset = 0.0
        
        for letter in LETTERS:
            # Store local relative coordinate vector
            pins[f"{num}_{letter}"] = (x_offset, y_offset, 0.0)
            
            # Advance X for the next letter in the row
            if letter == 'e':  # Transition across center divider (between 'e' and 'f')
                x_offset += X_DISTANCE_MIDDLE_PINS
            else:
                x_offset += X_DISTANCE_PINS
    return pins

def get_pin_world_location(grid_origin_name: str, pin_key: str, pin_dict: dict): 
    """ Returns the exact 3D world position for a pin key
    
    Parameters: 
            grid_origin_name: name of grid origin obj
            pin_key: eg, a_1
            pin_dict: dictionary in format {pin_key : local coordinate}
    
    local coordinate is relative to grid_origin
    """
    grid_origin = bpy.data.objects.get(grid_origin_name)
    local_tuple = pin_dict.get(pin_key)
    
    if local_tuple and grid_origin: 
        # Convert tuple to vector for matrix multiplication 
        local_vec = Vector(local_tuple)
        # Transforms local offset into current world coordinates 
        return grid_origin.matrix_world @ local_vec

    return None

# Example: 
pins_hashmap = get_pin_dict("Grid_Origin")

# Get world location of pin 5_e
world_pos_1a = get_pin_world_location("Grid_Origin", "5_e", pins_hashmap)
print(f"5_e World Position: {world_pos_1a}")
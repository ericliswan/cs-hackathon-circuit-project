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

    LETTERS = ('a', 'b', 'c', 'd', 'e', 'a', 'b', 'c', 'd', 'e')
    pins = {}

    for num in range(1, 61):
        # Calculate row Y offset (0-indexed shift from row 1)
        y_offset = (num - 1) * -Y_DISTANCE_PINS  
        x_offset = 0.0
        middle_jump = False
        
        for letter in LETTERS:
            # Store local relative coordinate vector
            if middle_jump is True: 
                pins[f"{num+60}{letter}"] = (x_offset, y_offset, 0.0)
            else: 
                pins[f"{num}{letter}"] = (x_offset, y_offset, 0.0)
            
            # Advance X for the next letter in the row
            if letter == 'e':  # Transition across center divider (between 'e' and 'f')
                x_offset += X_DISTANCE_MIDDLE_PINS
                middle_jump = True
            else:
                x_offset += X_DISTANCE_PINS

    # LHS of Breadboard (unfinished), intends to make RHS for power rails
    
    for num in range(1, 56): 
        y_starting_pos = -0.616
        x_starting_pos = -0.3763
        count = 1
        
        if num % 5: 
            y_offset = (num - 1) * -2*Y_DISTANCE_PINS
        else: 
            y_offset = (num - 1) * -Y_DISTANCE_PINS
            
        x_offset = 0.0
        
        for i in range(2): 
            if i % 2 == 0: 
                pins[f'GND'] = (x_offset, y_offset, 0.0)
            else: 
                x_offset += X_DISTANCE_PINS
                pins['VCC'] = (x_offset, y_offset, 0.0)
                
    return pins
    
        
    
    
def get_pin_world_location(grid_origin_name: str, pin_key: str, pin_dict: dict): 
    """ Returns the exact 3D world position for a pin key
    
    Parameters: 
            grid_origin_name: name of grid origin object
            pin_key: eg, a1
            pin_dict: dictionary in format {pin_key : local coordinate}
    
    local coordinate is relative to grid_origin
    """
    grid_origin = bpy.data.objects.get(grid_origin_name)
    local_tuple = pin_dict.get(pin_key)
    
    if local_tuple and grid_origin: 
        # Convert tuple to vector for matrix multiplication 
        local_vec = Vector(local_tuple)
        # Transforms local offset into current world coordinates
        return_vec = grid_origin.matrix_world @ local_vec
        return return_vec

    return None

def local_to_global_coordinates(grid_origin_name: str, pin_dict: dict): 
    """ Replaces local coordinates of pin dictionary to global.

    Parameters: 
        pin_dict(dict): dictionary in format {pin_key : local coordinate}
        grid_origin_name: name of grid origin object

    Returns: 
        pin_dict: dictionary in format {pin_key : global coordinate}
    """
    # Iterates over pin_dict and changes local coordinates to global coordinates.
    for pin_key, local_coordinate in pin_dict.items(): 
        pin_dict[pin_key] = get_pin_world_location(grid_origin_name, pin_key, pin_dict)

    return pin_dict


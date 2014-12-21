bl_info = {
    "name": "Mount Points Generator",
	"description": "Creats <MountPoint> XML for selected polygons with a material named 'MountPoint'",
	"author": "Harag",
	"version": (1, 0, 0),
	"location": "3D View > Object > Space Engineers",
	"wiki_url": "https://github.com/harag-on-steam/se-blender/wiki",
	"tracker_url": "https://github.com/harag-on-steam/se-blender/issues",
    "category": "Space Engineers",
}

import bpy
import re

from mathutils import Vector, Matrix
from math import sqrt
from collections import namedtuple
from enum import IntEnum


# just give proper axis names to the matrix indices
X = 0
Y = 1
Z = 2
W = 3

class BlockSize(IntEnum):
    LARGE = 5
    SMALL = 1

# constructs a sparse matrix from a list of tuples (col, row, value)
def sparse(values):
    result = Matrix()
    result.zero()
    result[W][W] = 1
    for cell in values:
        result.col[cell[0]][cell[1]] = cell[2]
    return result
    
# corner indices in terms of Object.bound_box,
# letter codes meaning Front/Back, Top/Bottom, Left/Right
# The directions are relative to a viewpoint in front of the object.
# So 'left' is 'left' in Blender but 'right' in Space Engineers and vice versa
class Corners(IntEnum):
    FBL = 0 
    FTL = 1
    BTL = 2
    BBL = 3
    FBR = 4
    FTR = 5
    BTR = 6
    BBR = 7

Side = namedtuple('Side', (
    'normal', # direction a polygon must be roughly facing to be considered on this side 
    'name', # name of the side as defined by <MountPoint Side=...>
    'projection', # converts this side's plane coordinates into XY-plane coordinates
    'start', # starting corner (origin) in terms of Object.bound_box
    'end', # end corner in terms of Object.bound_box
))

Sides = [
    Side( Vector(( 0,  0,  1)), 'Top',    sparse([(X,X,-1), (Y,Y,-1)]), Corners.BTR, Corners.FTL ),
    Side( Vector(( 0,  0, -1)), 'Bottom', sparse([(X,X,-1), (Y,Y, 1)]), Corners.FBR, Corners.BBL ),
    # right in Blender but left when viewed from the back (as SE does)
    Side( Vector(( 1,  0,  0)), 'Left',   sparse([(Y,X, 1), (Z,Y, 1)]), Corners.FBR, Corners.BTR ),
    # left in Blender but right when viewed from the back (as SE does)
    Side( Vector((-1,  0,  0)), 'Right',  sparse([(Y,X,-1), (Z,Y, 1)]), Corners.BBL, Corners.FTL ),
    Side( Vector(( 0, -1,  0)), 'Front',  sparse([(X,X, 1), (Z,Y, 1)]), Corners.FBL, Corners.FTR ),
    Side( Vector(( 0,  1,  0)), 'Back',   sparse([(X,X,-1), (Z,Y, 1)]), Corners.BBR, Corners.BTL ),
]

# returns a list of 8 vertices that define the bounding box around the 
# given set of vertices. The result is ordered in terms of Object.bound_box.
# @see Corners
def bounds(vertices):
    minX = minY = minZ = float('+inf')
    maxX = maxY = maxZ = float('-inf')
    
    for v in vertices:
        minX = min(minX, v[X])
        maxX = max(maxX, v[X])
        minY = min(minY, v[Y])
        maxY = max(maxY, v[Y])
        minZ = min(minZ, v[Z])
        maxZ = max(maxZ, v[Z])
    
    return [
        Vector((minX, minY, minZ)), #FBL
        Vector((minX, minY, maxZ)), #FTL
        Vector((minX, maxY, maxZ)), #BTL
        Vector((minX, maxY, minZ)), #BBL
        Vector((maxX, minY, minZ)), #FBR
        Vector((maxX, minY, maxZ)), #FTR
        Vector((maxX, maxY, maxZ)), #BTR
        Vector((maxX, maxY, minZ)), #BBR
    ]
     
# polygons are mount points if their material is named 'MountPoint'
def is_mount_point(ob, poly):
    return poly.material_index < len(ob.material_slots) and \
        ob.material_slots[poly.material_index].name == 'MountPoint'

# the dot-product of two vectors with an angle of 45 degrees between them
ANGLE_45 = sqrt(2.0) / 2.0

# This algorithm does the following
# 1. find all polygons with material 'MountPoint' from all selected objects
# 2. decide which SE block-side each polygon is facing by comparing polygon normals
# 3. project the bounding box of the polygon onto that block-side of the active object
# 4. convert that projection into the coordinate system of the corresponding SE block-side 
def mount_point_definitions(block_box, block_size, mount_point_objects):
    block_bounds = bounds([block_box.matrix_world * Vector(v) for v in block_box.bound_box])
    
    # normalizes sizes to one block-size.
    block_scale = Matrix.Scale(2 / block_size, 4)
    
    mount_points = []
    
    for ob in mount_point_objects:
        if ob.type != 'MESH': continue
        
        rotate_to_world = ob.matrix_world.to_3x3().normalized()
        
        # create temporary mesh with modifiers applied - including mirroring & array
        mesh = ob.to_mesh(bpy.context.scene, True, 'PREVIEW') 
        
        try:
            for poly in mesh.polygons:
                if not is_mount_point(ob, poly): continue

                polyside = None
                
                for side in Sides:
                    angle = rotate_to_world * poly.normal * side.normal
                    if angle > ANGLE_45:
                        polyside = side
                        break
                
                if not polyside: continue
                
                polybounds = bounds([ob.matrix_world * mesh.vertices[v].co for v in poly.vertices])
                
                start = polybounds[polyside.start] - block_bounds[polyside.start]
                end = polybounds[polyside.end] - block_bounds[polyside.start]

                start = block_scale * polyside.projection * start
                end = block_scale * polyside.projection * end
                
                mount_points.append((polyside.name, start.x, start.y, end.x, end.y))
                    
        finally:
            bpy.data.meshes.remove(mesh)
    
    return mount_points

NEGATIVE_ZERO = re.compile(r"-0\.00")
POSITIVE_ZERO = '0.00'
	
def mount_points_xml(mount_points):
    str = '<MountPoints>\n'

    for mp in mount_points:
        str += '\t<MountPoint Side="%s" StartX="%.2f" StartY="%.2f" EndX="%.2f" EndY="%.2f" />\n' % mp

    str += '</MountPoints>\n'
    return NEGATIVE_ZERO.sub(POSITIVE_ZERO, str)


# useful Display settings while editing Mount Points
#bpy.context.space_data.grid_scale = 1.25   
#bpy.context.space_data.grid_subdivisions = 5

class GenerateMountPointDefinitions(bpy.types.Operator):
    """Projects all polygons with material 'Mount Points' from all selected objects onto the sides of the active object.
Results are stored in the system clipboard."""
    bl_idname = "object.se_generate_mount_points"
    bl_label = "Copy Mount Points to Clipboard" 
    bl_options = {'REGISTER'}

    def execute(self, context):
        if not context.mode == 'OBJECT':
            self.report({'ERROR_INVALID_CONTEXT'}, 'Run this operator in Object Mode')
            return {'FINISHED'}
        
        if not context.active_object or len(context.selected_objects) == 0:
            self.report({'ERROR_INVALID_INPUT'}, 'Nothing selected')
            return {'FINISHED'}

        mount_points = mount_point_definitions(context.active_object, BlockSize.LARGE, context.selected_objects)
        
        if len(mount_points) == 0:
            self.report({'ERROR_INVALID_INPUT'}, 'Selected objects don\'t have polygons with material "MountPoint"')
            return {'FINISHED'}

        bpy.context.window_manager.clipboard = mount_points_xml(mount_points)
        self.report({'INFO'}, 'Created %d mount points in relation to object "%s" and copied them to the clipboard' % 
            (len(mount_points), context.active_object.name))

        return {'FINISHED'}

class SE3DViewObjectMenu(bpy.types.Menu):
    bl_idname = "VIEW3D_MT_object_space_engineers"
    bl_label = "Space Engineers"

    def draw(self, context):
        layout = self.layout

def se_submenu(self, context):
    self.layout.menu("VIEW3D_MT_object_space_engineers")

	
def se_gen_mountpoints(self, context):
    self.layout.operator("object.se_generate_mount_points")

def register():
    bpy.utils.register_module(__name__)

    bpy.types.VIEW3D_MT_object.append(se_submenu)
    bpy.types.VIEW3D_MT_object_space_engineers.append(se_gen_mountpoints)


def unregister():
    bpy.types.VIEW3D_MT_object_space_engineers.remove(se_gen_mountpoints)
    bpy.types.VIEW3D_MT_object.remove(se_submenu)
	
    bpy.utils.unregister_module(__name__)

if __name__ == "__main__":
    register()


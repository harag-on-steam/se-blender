from collections import namedtuple, OrderedDict
from xml.etree import ElementTree
from bgl import glEnable, glDisable, glColor3f, glVertex3f, glLineWidth, glBegin, glEnd, glLineStipple, GL_LINE_STRIP, GL_LINES, GL_LINE_STIPPLE
from mathutils import Matrix, Vector
from math import sqrt
from .utils import BoxCorner, bounds, sparse, X, Y, Z, transX, transY, transZ, rot, mirror, flip, layers, layer_bits, \
    scene
from .types import show_block_bounds, block_bounds, is_small_block, data

import bpy

MOUNT_POINT_MATERIAL = 'MountPoint'
MOUNT_POINT_COLOR = (0.317, 1, 0.032)

Side = namedtuple('Side', (
    'normal', # direction a polygon must be roughly facing to be considered on this side
    'name', # name of the side as defined by <MountPoint Side=...>
    'projection', # converts this side's plane coordinates into XY-plane coordinates
    'start_vertex', # starting corner (origin) in terms of Object.bound_box
    'end_vertex', # end corner in terms of Object.bound_box
))

# TODO re-formulate the projections in terms of mirror.<axis> and flip.<axies>
Sides = [
    Side( Vector(( 0,  0,  1)), 'Top',    sparse(((X,X,-1), (Y,Y,-1))), BoxCorner.BTR, BoxCorner.FTL ),
    Side( Vector(( 0,  0, -1)), 'Bottom', sparse(((X,X,-1), (Y,Y, 1))), BoxCorner.FBR, BoxCorner.BBL ),
    # right in Blender but left when viewed from the back (as SE does)
    Side( Vector(( 1,  0,  0)), 'Left',   sparse(((Y,X, 1), (Z,Y, 1))), BoxCorner.FBR, BoxCorner.BTR ),
    # left in Blender but right when viewed from the back (as SE does)
    Side( Vector((-1,  0,  0)), 'Right',  sparse(((Y,X,-1), (Z,Y, 1))), BoxCorner.BBL, BoxCorner.FTL ),
    Side( Vector(( 0, -1,  0)), 'Front',  sparse(((X,X, 1), (Z,Y, 1))), BoxCorner.FBL, BoxCorner.FTR ),
    Side( Vector(( 0,  1,  0)), 'Back',   sparse(((X,X,-1), (Z,Y, 1))), BoxCorner.BBR, BoxCorner.BTL ),
]

# the dot-product of two vectors with an angle of 45 degrees between them
ANGLE_45 = sqrt(2.0) / 2.0

def mount_point_definitions(mount_point_objects):
    """
    This algorithm does the following
    1. find all polygons with material 'MountPoint' from all given objects
    2. decide which SE block-side each polygon is facing by comparing polygon normals
    3. project the bounding box of the polygon onto that SE block-side
    4. convert that projection into the coordinate system of the corresponding SE block-side
    """

    # normalizes sizes to one block-cube
    normalize = Matrix.Scale(2.0 if is_small_block() else 0.4, 4)
    bound_box = block_bounds()

    mount_points = []

    def first(iterable): return next(iterable, None)

    for ob in mount_point_objects:
        if 'MESH' != ob.type: continue

        mp_mat_slot = first(slot for slot, mat in enumerate(ob.material_slots)
            if MOUNT_POINT_MATERIAL == mat.name)

        if None == mp_mat_slot: continue # 0 evaluates to False

        rotate_to_world = ob.matrix_world.to_3x3().normalized()

        # create temporary mesh with modifiers applied - including mirroring & array
        mesh = ob.to_mesh(scene(), True, 'PREVIEW')

        try:
            for poly in mesh.polygons:
                if not poly.material_index == mp_mat_slot: continue

                polyside = first(side for side in Sides
                    if (rotate_to_world * poly.normal * side.normal) > ANGLE_45)

                if not polyside: continue

                polybounds = bounds([ob.matrix_world * mesh.vertices[v].co for v in poly.vertices])

                start = polybounds[polyside.start_vertex] - bound_box[polyside.start_vertex]
                end = polybounds[polyside.end_vertex] - bound_box[polyside.start_vertex]

                start = normalize * polyside.projection * start
                end = normalize * polyside.projection * end

                mount_points.append((polyside.name, start.x, start.y, end.x, end.y))

        finally:
            bpy.data.meshes.remove(mesh)

    return mount_points

def _floatstr(f):
    return ("%.2f" % (f)).replace('-0.00', '0.00')

def mount_points_xml(mount_points):
    e = ElementTree.Element("MountPoints")

    for side, startx, starty, endx, endy in mount_points:
        mp = ElementTree.SubElement(e, "MountPoint")
        mp.attrib = OrderedDict([
            ('Side', side),
            ('StartX',_floatstr(startx)),
            ('StartY',_floatstr(starty)),
            ('EndX',_floatstr(endx)),
            ('EndY',_floatstr(endy)),
        ])

    return e

def create_mount_point_skeleton():
    quad = [
        (-0.75, 0, -0.75),
        ( 0.75, 0, -0.75),
        ( 0.75, 0,  0.75),
        (-0.75, 0,  0.75)
    ]

    x, y, z = block_bounds().btr

    transforms = [
       transY(-y)                ,
       transY( y) *  rot.z    ,
       transX( x) *  rot.halfz,
       transX(-x) * -rot.halfz,
       transZ(-z) *  rot.halfx,
       transZ( z) * -rot.halfx,
    ]

    verts = []
    faces = []
    idx = 0

    for transform in transforms:
        new_verts = [transform * Vector(v) for v in quad]
        new_face = (idx, idx+1, idx+2, idx+3)

        verts += new_verts
        faces.append(new_face)
        idx += 4

    try:
        mat = bpy.data.materials[MOUNT_POINT_MATERIAL]
    except KeyError:
        mat = bpy.data.materials.new(MOUNT_POINT_MATERIAL)
        mat.diffuse_color = MOUNT_POINT_COLOR

    mesh = bpy.data.meshes.new(MOUNT_POINT_MATERIAL)
    mesh.from_pydata(verts, [], faces)
    mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.material_index = 0
    mesh.update(calc_edges=True)

    ob = bpy.data.objects.new(MOUNT_POINT_MATERIAL, mesh)

    # bpy.context.space_data.grid_scale = 1.25
    # bpy.context.space_data.grid_subdivisions = 5
    # bpy.context.space_data.grid_lines = 21

    return ob


# ------------------------------------ draw block bounding box ------------------------------------------- #


def mount_point_color():
    try:
        mat = bpy.data.materials[MOUNT_POINT_MATERIAL]
        return Vector(mat.diffuse_color)
    except KeyError:
        return MOUNT_POINT_COLOR

def draw_box(verts):
    glBegin(GL_LINE_STRIP)
    for i in 0, 1, 2, 3, 0, 4, 5, 6, 7, 4:
        glVertex3f(*verts[i])
    glEnd()

    # the strip covers 9 of the 12 edges
    glBegin(GL_LINES)
    glVertex3f(*verts[1])
    glVertex3f(*verts[5])

    glVertex3f(*verts[2])
    glVertex3f(*verts[6])

    glVertex3f(*verts[3])
    glVertex3f(*verts[7])
    glEnd()

def draw_block_box():
    if not show_block_bounds():
        return

    box = block_bounds()
    color = bpy.context.user_preferences.themes[0].view_3d.object_selected

    glLineWidth(1.0)
    glColor3f(*color)
    glLineStipple(1, 0x3333)
    glEnable(GL_LINE_STIPPLE)
    draw_box(box)
    glDisable(GL_LINE_STIPPLE)

def tag_view3d_for_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()

handle_block_box = None

def enable_draw_callback():
    global handle_block_box

    if not handle_block_box:
        handle_block_box = bpy.types.SpaceView3D.draw_handler_add(draw_block_box, (), 'WINDOW', 'POST_VIEW')

    tag_view3d_for_redraw()


def disable_draw_callback():
    global handle_block_box

    if handle_block_box:
        bpy.types.SpaceView3D.draw_handler_remove(handle_block_box, 'WINDOW')
        handle_block_box = None

    tag_view3d_for_redraw()

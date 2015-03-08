from collections import namedtuple, OrderedDict
from mathutils import Vector, Matrix, Euler
from math import radians as rad
import math
import re
import bpy
from .utils import layers, layer_bits, layer_bit

mirroring = OrderedDict([
    ('None', (0.0, 0.0, 0.0)),
    ('X', (180.0, 0.0, 0.0)),
    ('Y', (0.0, 0.0, 180.0)),
    ('Z', (0.0, 180.0, 0.0)),
    ('HalfX', (90.0, 0.0, 0.0)),
    ('HalfY', (0.0, 0.0, -90.0)),
    ('HalfZ', (0.0, -90.0, 0.0)),
    ('MinusHalfX', (-90.0, 0.0, 0.0)),
    ('MinusHalfY', (0.0, 0.0, 90.0)),
    ('MinusHalfZ', (0.0, 90.0, 0.0)),
    ('XHalfY', (180.0, 0.0, -90.0)),
    ('XHalfZ', (180.0, 90.0, 0.0)),
    ('YHalfX', (90.0, 0.0, 180.0)),
    ('YHalfZ', (0.0, -90.0, 180.0)),
    ('ZHalfX', (-90.0, 0.0, 180.0)),
    ('ZHalfY', (0.0, 180.0, -90.0)),
    ('UnsupportedXY1', (90.0, 0.0, 90.0)),
    ('UnsupportedXY2', (-90.0, 0.0, 90.0)),
    ('UnsupportedXY3', (90.0, 0.0, -90.0)),
    ('UnsupportedXY4', (-90.0, 0.0, -90.0)),
    ('UnsupportedXZ1', (90.0, 90.0, 0.0)),
    ('UnsupportedXZ2', (-90.0, 90.0, 0.0)),
    ('UnsupportedXZ3', (90.0, -90.0, 0.0)),
    ('UnsupportedXZ4', (-90.0, -90.0, 0.0)),
])

def eulerTo3x3(eulerXYZ):
    if eulerXYZ is None: return None
    rotMatrix = eulerXYZ.to_matrix()
    return (
        rotMatrix.row[0].to_tuple(4),
        rotMatrix.row[1].to_tuple(4),
        rotMatrix.row[2].to_tuple(4),
    )

enumToMatrix = { enum : eulerTo3x3(Euler((math.radians(a) for a in angles))) for enum, angles in mirroring.items() }
matrixToEnum = { matrix : enum for enum, matrix in enumToMatrix.items() }

RE_MIRROR = re.compile(
    r"^Mirror(?:ing)?("
    r"(X|LR|RL|Left|LeftRight|RightLeft|Right|Side)|"
    r"(Y|TB|BT|Top|TopBottom|BottomTop|Bottom)|"
    r"(Z|FB|BF|Front|FrontBack|BackFront|Back))",
    flags=re.IGNORECASE)

Mirroring = namedtuple('Mirroring', ('axis', 'enum'))

def mirroringAxisFromObjectName(ob) -> str:
    match = RE_MIRROR.search(ob.name)
    if not match:
        return None
    return 'X' if match.group(2) else 'Y' if match.group(3) else 'Z'

def mirroringFromObject(ob) -> str:
    return matrixToEnum.get(eulerTo3x3(ob.rotation_euler), 'NonRectangular')

# -------------------------------------------------------------------------------------------------------------------- #

mirroringUI = [
    (enum, enum, "", "MOD_MIRROR", i)
    for i, (enum, angles) in enumerate(mirroring.items())
    if not enum.startswith("Unsupported")
]
mirroringUI += [
    ('Unsupported', 'Unsupported by SE', '', 'ERROR', len(mirroringUI)+0),
    ('NonRectangular', 'Non-rectangular', '', 'ERROR', len(mirroringUI)+1),
]
mirroringEnumToIndex = {ui[0] : ui[4] for ui in mirroringUI}

def setMirroringProp(self, value):
    enum = mirroringUI[value][0]
    if enum == 'Unsupported' or enum == 'NonRectangular':
        return
    self.rotation_euler = Euler((math.radians(a) for a in mirroring[enum]))

def getMirroringProp(self):
    enum = mirroringFromObject(self)
    return mirroringEnumToIndex[enum if not enum.startswith('Unsup') else 'Unsupported']

# This property must be placed directly on bpy.types.Object because it is derived from Object.rotation_euler
mirroringProperty = bpy.props.EnumProperty(
    items=mirroringUI,
    name="Mirroring",
    description="Mirroring-rotation; empty needs to be named 'MirrorFrontBottom', 'MirrorTopBottom' or 'MirrorLeftRight'  ",
    options=set(),
    get=getMirroringProp,
    set=setMirroringProp,
)

MIRRORS = {
    'X' : ('MirrorLeftRight', Vector((-1, 0, 0))),
    'Y' : ('MirrorTopBottom', Vector(( 0, 0, 1))),
    'Z' : ('MirrorFrontBack', Vector(( 0, 1, 0))),
}

def setupMirrors(scene: bpy.types.Scene, mainObjects=[], blockSize=(1,1,1), isSmall=False, layer=-1):
    cubeSize = 0.5 if isSmall else 2.5
    maxBlockSize = max(blockSize) * cubeSize
    mirrorDistance = maxBlockSize + cubeSize
    mirrorLayer = layers(layer_bit(scene.active_layer if layer == -1 else layer))

    mirrors = { 'X' : None, 'Y' : None, 'Z' : None }

    for ob in scene.objects:
        axis = mirroringAxisFromObjectName(ob)
        if not axis is None and mirrors[axis] is None:
            mirrors[axis] = ob

    for axis, mirror in mirrors.items():
        if mirror is None:
            mirror = mirrors[axis] = bpy.data.objects.new(MIRRORS[axis][0], None)
            mirror.empty_draw_type = 'ARROWS'
            mirror.show_x_ray = True
            scene.objects.link(mirror)
        mirror.empty_draw_size = cubeSize
        mirror.location = MIRRORS[axis][1] * mirrorDistance
        mirror.layers = mirrorLayer # needs to be done after the object is in the scene

    # need these more than once but generators only evaluate once
    mainObjects = [o for o in mainObjects]
    mirrors = {m for m in mirrors.values()}
    removed = set() # don't re-add old mesh copies

    for mirror in mirrors:
        for c in mirror.children:
            try:
                scene.objects.unlink(c)
            except RuntimeError:
                pass # already removed from the scene
            c.parent = None
            removed.add(c)

    for mirror in mirrors:
        for o in mainObjects:
            if o.type == 'MESH' and not o.parent in mirrors and not o in removed:
                copy = o.copy()
                copy.parent = mirror
                copy.hide_select = True
                scene.objects.link(copy)
                copy.layers = mirrorLayer # needs to be done after the object is in the scene

    scene.update()
import bpy
from mathutils import Vector
from .nodes import LayerObjectsNode, SeparateLayerObjectsNode, MwmFileNode, HavokFileNode, \
    TemplateStringNode, BlockDefinitionNode, BlockExportTree
from .utils import layers


def newCombinedLayers(tree, label="", location=(0,0), layer_mask=0b10000000000000000000):
    layer = tree.nodes.new(LayerObjectsNode.bl_idname)
    layer.label = label
    layer.location = Vector(location)
    layer.layer_mask = layers(layer_mask)
    return layer.outputs[0]

def newSeparateLayers(tree, label=None, location=(0,0), layer_mask=0b10000000000000000000):
    layer = tree.nodes.new(SeparateLayerObjectsNode.bl_idname)
    if not label is None:
        layer.label = label
    layer.location = Vector(location)
    layer.layer_mask = layers(layer_mask)
    return [o for o in layer.outputs if o.enabled]

def newMwmBuilder(tree, label=None, location=(0,0), name=None, objects=None, physics=None, lods=[]):
    mwm = tree.nodes.new(MwmFileNode.bl_idname)
    if not label is None:
        mwm.label = label
    mwm.location = Vector(location)
    if not name is None:
        if isinstance(name, bpy.types.NodeSocket):
            tree.links.new(name, mwm.inputs['Name'])
        else:
            mwm.inputs['Name'].text = name
    if not objects is None:
        tree.links.new(objects, mwm.inputs['Objects'])
    if not physics is None:
        tree.links.new(physics, mwm.inputs['Havok'])
    lodInputSockets = [s for s in mwm.inputs if s.name.startswith('LOD')]
    for lod, socket in zip(lods, lodInputSockets):
        tree.links.new(lod[0], socket)
        socket.distance = lod[1]
    for s in mwm.inputs:
        if not s.name == 'Name' and not s.is_linked:
            s.hide = True
    return mwm.outputs[0]

def newHavokConverter(tree, label=None, location=(0,0), name=None, objects=None):
    havok = tree.nodes.new(HavokFileNode.bl_idname)
    havok.location = Vector(location)
    if not label is None:
        havok.label = label
    if not name is None:
        if isinstance(name, bpy.types.NodeSocket):
            tree.links.new(name, havok.inputs['Name'])
        else:
            havok.inputs['Name'].text = name
    if not objects is None:
        tree.links.new(objects, havok.inputs['Objects'])
    return havok.outputs[0]

def newText(tree, label=None, location=(0,0), text=""):
    txt = tree.nodes.new(TemplateStringNode.bl_idname)
    if not label is None:
        txt.label = label
    txt.location = location
    txt.outputs[0].text = text
    txt.width = 190.0
    return txt.outputs[0]

def newBlockDef(tree, label=None, location=(0,0), model=None, mountPoints=None, mirroring=None, constrs=[]):
    bd = tree.nodes.new(BlockDefinitionNode.bl_idname)
    bd.location = Vector(location)
    bd.width = 220.0
    if not label is None:
        bd.label = label
    if not model is None:
        tree.links.new(model, bd.inputs['Main Model'])
    if not mountPoints is None:
        tree.links.new(mountPoints, bd.inputs['Mount Points'])
    if not mirroring is None:
        tree.links.new(mirroring, bd.inputs['Mirroring'])
    constrSockets = [s for s in bd.inputs if s.name.startswith('Constr')]
    for constr, socket in zip(constrs, constrSockets):
        tree.links.new(constr, socket)
    for s in bd.inputs:
        if s.name.startswith("Constr") and not s.is_linked:
            s.hide = True

def createDefaultTree(tree: BlockExportTree):
    layerMain   = newCombinedLayers(tree, "Main Model",          (-777,  506), 0b10000000000000000000)
    layerPhys   = newCombinedLayers(tree, "Collision",           (-777,  361), 0b01000000000000000000)
    layerLOD    = newSeparateLayers(tree, "Level of Detail",     (-777,  216), 0b00000111000000000000)
    layerConstr = newSeparateLayers(tree, "Construction Phases", (-777, - 96), 0b00000000001110000000)
    layerMP     = newCombinedLayers(tree, "Mount Points",        ( 173,  283), 0b00100000000000000000)
    layerMirror = newCombinedLayers(tree, "Mirroring",           ( 173,  134), 0b00010000000000000000)

    physics = newHavokConverter(tree, "Havok", (-516, 412), '${SubtypeId}', layerPhys)

    nameLOD = newText(tree, "LOD Name", (-564, 82), '${SubtypeId}_LOD${n}')
    mwmLODs = [newMwmBuilder(tree, "Mwm LOD%d" % (i+1), (-105, 392 - 128*i), nameLOD, o, physics)
               for i, o in enumerate(layerLOD)]

    mwmMain = newMwmBuilder(tree, "Mwm Main", (173, 551), '${SubtypeId}', layerMain, physics,
                            [(s,d) for s, d in zip(mwmLODs, [10,30,50])])

    nameConstr = newText(tree, "Construction Name", (-564, -9), '${SubtypeId}_Constr${n}')
    mwmConstrs = [newMwmBuilder(tree, "Mwm Constr%d" % (i+1), (-105, -21 - 128*i), nameConstr, o, physics)
               for i, o in enumerate(layerConstr)]

    newBlockDef(tree, None, (473, 105), mwmMain, layerMP, layerMirror, mwmConstrs)

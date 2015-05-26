from itertools import chain
import bpy
import shutil
from os.path import join
from os import makedirs
from subprocess import CalledProcessError
from string import Template
from .mirroring import mirroringAxisFromObjectName
from .types import sceneData, data
from .utils import layer_bits, layer_bit, scene, first, PinnedScene, reportMessage
from .export import ExportSettings, export_fbx, fbx_to_hkt, hkt_filter, write_pretty_xml, mwmbuilder, generateBlockDefXml
from .mwmbuilder import material_xml, mwmbuilder_xml, lod_xml

COLOR_OBJECTS_SKT  = (.50, .65, .80, 1)
COLOR_OBJECTS_WND  = (.45, .54, .61)
COLOR_TEXT_SKT     = (.90, .90, .90, 1)
COLOR_TEXT_WND     = (.66, .66, .66)
COLOR_HKT_SKT      = (.60, .90, .40, 1)
COLOR_HKT_WND      = (.55, .69, .50)
COLOR_MWM_SKT      = (  1, .70, .30, 1)
COLOR_MWM_WND      = (.70, .56, .42)
COLOR_BLOCKDEF_WND = (  1, .98, .52)

OBJECT_TYPES = {'EMPTY', 'MESH'}

class BlockExportTree(bpy.types.NodeTree):
    bl_idname = "SEBlockExportTree"
    bl_label = "Block Export Settings"
    bl_icon = "SCRIPTPLUGINS"
    type = "CUSTOM"

    def getAllMwmObjects(self):
        return chain.from_iterable((n.inputs['Objects'].getObjects() for n in self.nodes if isinstance(n, MwmFileNode)))

class ObjectSource:
    '''
        Enumerates scene-objects for a requesting socket
    '''

    def getObjects(self, socket: bpy.types.NodeSocket):
        return []

class ParamSource:
    '''
        A source of string-template substitution parameters
    '''

    def getParams(self) -> dict:
        return {}

class TextSource:
    '''
        Provides a string that can have parameters substituted.
    '''

    def getText(self, *args, **params) -> str:
        return ""

class Exporter:
    '''
        Does an export job within the given context, possibly caching the result.
    '''

    def export(self, exportContext):
        raise NotImplementedError("No export implemented")

class ReadyState:

    def isReady(self):
        return True

class Upgradable:
    def upgrade(self):
        pass

# -------------------------------------------------------------------------------------------------------------------- #

class SESocket:
    def draw(self, context, layout, node, text):
        '''Do not override. Override drawChecked() instead.'''
        source = self.firstSource()
        if not source is None and not self.isCompatibleSource(source):
            layout.label(text="incompatible", icon="ERROR")
            return

        self.drawChecked(context, layout, node, text, source)

    def drawChecked(self, context, layout, node, text, source):
        '''Only called if a linked source was already determined to be compatible.'''
        layout.label(text=text)

    def draw_color(self, context, node):
        '''Draws the socket colored according to attribute bl_color or red if the linked source is incompatible.'''
        source = self.firstSource()
        if not source is None and not self.isCompatibleSource(source):
            return (1, 0, 0, 1)

        return self.drawColorChecked(context, node, source)

    def drawColorChecked(self, context, node, source):
        r, g, b, a = self.bl_color
        return (r, g, b, a if self.is_linked else a * 0.6)

    def isCompatibleSource(self, socket):
        '''Decide if the give socket is a compatible source for this socket.
        By default it is checked to have the same type.'''
        return self.bl_idname == socket.bl_idname

    def firstSource(self, named=None, type=None):
        '''Finds the first providing socket linked to this socket with the give name and type.'''
        if self.is_linked:
            for link in self.links:
                if link.from_socket != self \
                        and (named is None or link.from_socket.name == named) \
                        and (type is None or isinstance(link.from_socket, type)):
                    return link.from_socket
        return None

    def firstSink(self, named=None, type=None):
        '''Finds the first receiving socket linked to this socket with the give name and type.'''
        if self.is_linked:
            for link in self.links:
                if link.to_socket != self \
                        and (named is None or link.from_socket.name == named) \
                        and (type is None or isinstance(link.from_socket, type)):
                    return link.to_socket
        return None

class TextSocket(SESocket, TextSource):
    type = "STRING"

    show_editor_if_unlinked = bpy.props.BoolProperty(default=False)
    '''Shows an editor for the sockets 'text'-property if this socket is an input socket and is not linked.'''

    text = bpy.props.StringProperty()
    '''Provides the socket's string value directly. This is the last resort.'''
    node_input = bpy.props.StringProperty()
    '''Gets the string value from the owning node's named input-socket'''
    node_property = bpy.props.StringProperty()
    '''Gets the string value from the owning node's named property'''

    def getText(self, *args, **kwargs) -> str:
        '''
        Gets the string value from (in that order of precedence):
        1. a linked TextSource
        2. another input-socket of the node if configured,
        3. a property of the node if configured
        4. from the sockets 'text'-property
        '''
        if not self.enabled:
            return ""

        template = None

        source = self.firstSource(type=TextSource)
        if not source is None:
            template = Template(source.getText(**kwargs))

        if template is None and self.node_input:
            inputSocket = self.node.inputs[self.node_input]
            if isinstance(inputSocket, TextSource):
                template = Template(inputSocket.getText(**kwargs))

        if template is None and self.node_property:
            template = Template(getattr(self.node, self.node_property))

        if template is None:
            template = Template(self.text)

        params = self.getParams()
        params.update(kwargs)
        return template.safe_substitute(*args, **params)

    def getParams(self):
        params = {}

        for input in self.node.inputs:
            if not input is self and isinstance(input, ParamSource):
                params.update(input.getParams())

        return params

    def isReady(self):
        return not self.is_linked or self.isCompatibleSource(self.firstSource())

    def drawChecked(self, context, layout, node, text, source):
        if not self.is_output and source is None and self.show_editor_if_unlinked:
            layout.prop(self, "text", text="")
            return

        super().drawChecked(context, layout, node, text, source)

class ExportSocket(SESocket, Exporter):
    def export(self, settings: ExportSettings):
        '''Delegates the export to a linked source-socket if this is an input-socket
        or to the node if this is an output-socket.

        The first case fails with a ValueError if the socket is not linked.
        The second fails with a AttributeError if the socket is not placed on an Exporter node.'''
        if self.is_output:
            if not isinstance(self.node, Exporter):
                raise AttributeError("%s is not on an exporter node" % self.path_from_id())
            return self.node.export(settings)

        source = self.firstSource(type=Exporter)
        if source is None:
            raise ValueError("%s is not linked to an exporting source" % self.path_from_id())

        return source.export(settings)

class ObjectsSocket(SESocket, ObjectSource, ParamSource, ReadyState):
    n = bpy.props.IntProperty(default=-1)
    layer = bpy.props.IntProperty()

    def getObjects(self, socket: bpy.types.NodeSocket=None):
        if not self.enabled:
            return []

        elif self.is_output:
            if isinstance(self.node, ObjectSource):
                return self.node.getObjects(self)

        elif self.is_linked:
            fromSocket = self.firstSource()
            if isinstance(fromSocket, ObjectSource):
                return fromSocket.getObjects(self)

        return []

    def getN(self):
        source = self.firstSource(type=ObjectsSocket)
        if not source is None:
            return source.getN()
        return self.n

    def getParams(self):
        n = self.getN()
        return {'n': str(n)} if n > 0 else {}

    def isReady(self):
        return not self.is_linked or self.isCompatibleSource(self.firstSource())

    def isCompatibleSource(self, socket):
        return isinstance(socket, ObjectSource)

    def isEmpty(self):
        isEmpty = not any(o for o in self.getObjects())
        return isEmpty

    def drawColorChecked(self, context, node, source):
        color = super().drawColorChecked(context, node, source)
        if self.is_linked and self.isEmpty():
            color = (0.35, 0.35, 0.35, 1)
        return color

class FileSocket(TextSocket, ReadyState):
    def isCompatibleSource(self, socket):
        return isinstance(socket, type(self)) # or isinstance(socket, TemplateStringSocket)

    def isReady(self):
        if self.is_output:
            isNodeReady = not isinstance(self.node, ReadyState) or self.node.isReady()
            return isNodeReady

        source = self.firstSource(type=ReadyState)
        if not source is None:
            return source.isReady()

        return False

    def drawColorChecked(self, context, node, source):
        color = super().drawColorChecked(context, node, source)
        if self.is_linked and not self.isReady():
            color = (0.35, 0.35, 0.35, 1)
            # r, g, b, a = color
            # color = (r, g, b, a * 0.2)
        return color

# -------------------------------------------------------------------------------------------------------------------- #

class TemplateStringSocket(bpy.types.NodeSocket, TextSocket):
    bl_idname = "SETemplateStringSocket"
    bl_label = "Text"
    bl_color = COLOR_TEXT_SKT

    show_editor_if_unlinked = bpy.props.BoolProperty(default=True)

    def isCompatibleSource(self, socket):
        return isinstance(socket, TextSocket)

class MwmFileSocket(bpy.types.NodeSocket, FileSocket, ExportSocket):
    bl_idname = "SEMwmFileSocket"
    bl_label = ".mwm"
    bl_color = COLOR_MWM_SKT

# according to VRageRender.MyRenderModel.LoadData()
RENDER_QUALITIES = [
    ('LOW', 'Low', 'Low'),
    ('NORMAL', 'Norm', 'Normal'),
    ('HIGH', 'High', 'High'),
    ('EXTREME', 'Extr', 'Extreme'),
]

class LodInputSocket(bpy.types.NodeSocket, FileSocket, ExportSocket):
    bl_idname = "SELodInputSocket"
    bl_label = "LOD"
    bl_color = COLOR_MWM_SKT

    distance = bpy.props.IntProperty(
        name="Distance", default=10, min=0,
        description="The distance at which to switch to this level-of-detail")
    use_qualities = bpy.props.BoolProperty(
        name="Use Qualities", default=False,
        description="Should this level-of-detail only be used with a subset of render quality profiles?")
    qualities = bpy.props.EnumProperty(
        name="Render Quality",
        items=RENDER_QUALITIES, default={q[0] for q in RENDER_QUALITIES}, options={'ENUM_FLAG'},
        description="Mark the render quality profiles this level-of-detail should be used at")

    def drawChecked(self, context, layout, node, text, source):
        if self.is_linked:
            col = layout.column()

            row = col.row(align=True)
            row.prop(self, "distance")
            row.prop(self, "use_qualities", icon_only=True, icon='MOD_DECIM')

            if self.use_qualities:
                row = col.row()
                row.prop(self, "qualities")

            return

        super().drawChecked(context, layout, node, text, source)

    def isCompatibleSource(self, socket):
        return isinstance(socket, MwmFileSocket) # or isinstance(socket, TemplateStringSocket)

class HktFileSocket(bpy.types.NodeSocket, FileSocket, ExportSocket):
    bl_idname = "SEHktFileSocket"
    bl_label = ".hkt"
    bl_color = COLOR_HKT_SKT

class ObjectListSocket(bpy.types.NodeSocket, ObjectsSocket):
    bl_idname = "SEObjectListSocket"
    bl_label = "Objects"
    bl_color = COLOR_OBJECTS_SKT
    type = 'CUSTOM'

class RigidBodyObjectsSocket(bpy.types.NodeSocket, ObjectsSocket):
    '''selects only objects that have rigid-body settings'''
    bl_idname = "SERigidBodyObjectsSocket"
    bl_label = "Objects"
    bl_color = COLOR_OBJECTS_SKT
    type = 'CUSTOM'

    def getObjects(self, socket: bpy.types.NodeSocket=None):
        return (o for o in super().getObjects(socket) if not o.rigid_body is None)

class MountPointObjectsSocket(bpy.types.NodeSocket, ObjectsSocket):
    '''selects only objects that have a 'MountPoint' material'''
    bl_idname = "SEMountPointObjectsSocket"
    bl_label = "Objects"
    bl_color = COLOR_OBJECTS_SKT
    type = 'CUSTOM'

    def getObjects(self, socket: bpy.types.NodeSocket=None):
        return (o for o in super().getObjects(socket) if 'MountPoint' in o.material_slots)

class MirroringObjectsSocket(bpy.types.NodeSocket, ObjectsSocket):
    '''selects only objects that are name 'Mirror(ing)...' '''
    bl_idname = "SEMirroringObjectsSocket"
    bl_label = "Objects"
    bl_color = COLOR_OBJECTS_SKT
    type = 'CUSTOM'

    def getObjects(self, socket: bpy.types.NodeSocket=None):
        return (o for o in super().getObjects(socket) if not mirroringAxisFromObjectName(o) is None)

# -------------------------------------------------------------------------------------------------------------------- #

class SENode:
    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == BlockExportTree.bl_idname

class TemplateStringNode(bpy.types.Node, SENode):
    bl_idname = "SETemplateStringNode"
    bl_label = "Text with Parameters"
    bl_icon = "TEXT"

    def init(self, context):
        self.outputs.new(TemplateStringSocket.bl_idname, "Text")
        self.use_custom_color = True
        self.color = COLOR_TEXT_WND

    def draw_buttons(self, context, layout):
        if len(self.outputs) > 0:
            layout.prop(self.outputs['Text'], "text", text="")

class HavokFileNode(bpy.types.Node, SENode, Exporter, ReadyState):
    bl_idname = "SEHavokFileNode"
    bl_label = "Havok Converter"
    bl_icon = "PHYSICS"

    def init(self, context):
        self.inputs.new(TemplateStringSocket.bl_idname, "Name")
        self.inputs.new(RigidBodyObjectsSocket.bl_idname, "Objects")
        self.outputs.new(HktFileSocket.bl_idname, "Havok").node_input = "Name"

        self.use_custom_color = True
        self.color = COLOR_HKT_WND
        self.width_hidden = 87.0
        # self.hide = True

    def isReady(self):
        objects = self.inputs['Objects']
        hasObjects = objects.isReady() and not objects.isEmpty()

        name = self.inputs['Name']
        hasName = name.isReady() and name.getText()

        return hasObjects and hasName

    def export(self, settings: ExportSettings):
        name = self.inputs['Name'].getText(settings)
        if not name:
            settings.error("no name to export under", node=self)
            return 'SKIPPED'

        hktfile = join(settings.outputDir, name + ".hkt")
        fbxfile = join(settings.outputDir, name + ".hkt.fbx")

        if hktfile in settings.cache:
            return settings.cache[hktfile]

        objectsSource = self.inputs['Objects']
        if objectsSource.isEmpty():
            settings.text("layers had no collision-objects for export", file=hktfile, node=self)
            return settings.cacheValue(hktfile, 'SKIPPED')

        export_fbx(settings, fbxfile, objectsSource.getObjects())
        try:
            fbx_to_hkt(settings, fbxfile, hktfile)
            hkt_filter(settings, hktfile, hktfile)
        except CalledProcessError as e:
            settings.error(str(e), file=hktfile, node=self)
            return settings.cacheValue(hktfile, 'FAILED')

        settings.info("export successful", file=hktfile, node=self)
        return settings.cacheValue(hktfile, 'SUCCESS')


class MwmFileNode(bpy.types.Node, SENode, Exporter, ReadyState):
    bl_idname = "SEMwmFileNode"
    bl_label = "MwmBuilder"
    bl_icon = "EXPORT"

    def init(self, context):
        self.inputs.new(TemplateStringSocket.bl_idname, "Name")
        self.inputs.new(ObjectListSocket.bl_idname, "Objects")
        self.inputs.new(HktFileSocket.bl_idname, "Havok")
        self.outputs.new(MwmFileSocket.bl_idname, "Mwm").node_input = "Name"

        for i in range(1,11):
            self.inputs.new(LodInputSocket.bl_idname, "LOD %d" % (i))

        self.use_custom_color = True
        self.color = COLOR_MWM_WND

    def update(self):
        pins = [p for p in self.inputs.values() if p.name.startswith('LOD')]

        for i in range(len(pins)-1, 0, -1):
            pins[i].enabled = pins[i].is_linked or pins[i-1].is_linked
            if (pins[i].enabled):
                break

    def isReady(self):
        hasObjects = not self.inputs['Objects'].isEmpty()
        hasName = self.inputs['Name'].getText()
        # Havok is not required
        return hasObjects and hasName

    def export(self, settings: ExportSettings):
        name = self.inputs['Name'].getText(settings)
        if not name:
            settings.error("no name to export under", node=self)
            return 'SKIPPED'

        mwmfile = join(settings.outputDir, name + ".mwm")
        if mwmfile in settings.cache:
            return settings.cache[mwmfile]

        objectsSource = self.inputs['Objects']
        if objectsSource.isEmpty():
            settings.text("layers had no objects for export", file=mwmfile, node=self)
            return settings.cacheValue(mwmfile, 'SKIPPED')

        materials = {}
        for o in objectsSource.getObjects():
            for ms in o.material_slots:
                if not ms is None and not ms.material is None:
                    materials[ms.material.name] = ms.material
        materials_xml = [material_xml(settings, m) for m in materials.values()]

        sockets = [s for s in self.inputs if s.name.startswith("LOD") and s.enabled and s.is_linked]
        lods_xml = []
        msgs = []
        for i, socket in enumerate(sockets):
            lodName = socket.getText(settings)
            if socket.isReady() and socket.export(settings) == 'SUCCESS':
                lodDistance = socket.distance
                renderQualities = socket.qualities if socket.use_qualities else None
                lods_xml.append(lod_xml(settings, lodName, lodDistance, renderQualities))
            else:
                # report skips grouped after the export of dependencies
                msgs.append("socket '%s' not ready, skipped" % (socket.name))
        for msg in msgs:
            settings.text(msg, file=mwmfile, node=self)

        paramsfile = join(settings.outputDir, name + ".xml")
        paramsxml = mwmbuilder_xml(settings, materials_xml, lods_xml)
        write_pretty_xml(paramsxml, paramsfile)

        havokfile = None
        socket = self.inputs['Havok']
        if socket.isReady() and socket.export(settings) == 'SUCCESS':
            sourceName = socket.getText(settings)
            havokfile = join(settings.outputDir, sourceName + ".hkt")
        else:
            settings.info("no collision data included", file=mwmfile, node=self)

        fbxfile = join(settings.outputDir, name + ".fbx")
        export_fbx(settings, fbxfile, objectsSource.getObjects())

        try:
            mwmbuilder(settings, fbxfile, havokfile, paramsfile, mwmfile)
        except CalledProcessError as e:
            settings.error(str(e), file=mwmfile, node=self)
            return settings.cacheValue(mwmfile, 'FAILED')

        settings.info("export successful", file=mwmfile, node=self)
        return settings.cacheValue(mwmfile, 'SUCCESS')

class LayerObjectsNode(bpy.types.Node, SENode, ObjectSource):
    bl_idname = "SELayerObjectsNode"
    bl_label = "Combined Layers"
    bl_icon = "GROUP"
    bl_width_default = 170.0

    layer_mask = bpy.props.BoolVectorProperty(name="Layers", subtype='LAYER', size=20, default=([False] * 20))

    def init(self, context):
        pin = self.outputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def draw_buttons(self, context, layout):
        layout.prop(self, 'layer_mask')

    def getObjects(self, socket: ObjectListSocket):
        mask = layer_bits(self.layer_mask)
        return (obj for obj in scene().objects
            if obj.type in OBJECT_TYPES and (layer_bits(obj.layers) & mask) != 0)

class SeparateLayerObjectsNode(bpy.types.Node, SENode, ObjectSource):
    bl_idname = "SESeparateLayerObjectsNode"
    bl_label = "Separate Layers"
    bl_icon = "GROUP"
    bl_width_default = 170.0

    def onLayerMaskUpdate(self, context):
        mask = self.layer_mask
        ordinal = 1

        for i, pin in enumerate(self.outputs.values()):
            pin.enabled = mask[i]
            if pin.enabled:
                pin.n = ordinal
                pin.name = "Layer %02d \u2192 %d" % (i+1, ordinal)
                ordinal += 1

    layer_mask = bpy.props.BoolVectorProperty(name="Layers", subtype='LAYER', size=20, default=([False] * 20),
                                              update=onLayerMaskUpdate)

    def init(self, context):
        for i in range(0,20):
            pin = self.outputs.new(ObjectListSocket.bl_idname, "Layer %02d" % (i+1))
            pin.enabled = False
            pin.layer = i
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def draw_buttons(self, context, layout):
        layout.prop(self, 'layer_mask')

    def getObjects(self, socket: ObjectListSocket):
        mask = layer_bit(socket.layer)
        return (obj for obj in scene().objects
            if obj.type in OBJECT_TYPES and (layer_bits(obj.layers) & mask) != 0)

class BlockDefinitionNode(bpy.types.Node, SENode, Exporter, ReadyState, Upgradable):
    bl_idname = "SEBlockDefNode"
    bl_label = "Block Definition"
    bl_icon = "SETTINGS"

    def init(self, context):
        inputs = self.inputs
        inputs.new(MwmFileSocket.bl_idname, "Main Model")
        inputs.new(MountPointObjectsSocket.bl_idname, "Mount Points")
        inputs.new(MirroringObjectsSocket.bl_idname, "Mirroring")

        for i in range(1,11):
            inputs.new(MwmFileSocket.bl_idname, "Constr. Phase %d" % (i))

        self.use_custom_color = True
        self.color = COLOR_BLOCKDEF_WND

    def upgrade(self):
        inputs = self.inputs

        # new in v0.5.0
        if inputs.get('Mirroring', None) is None:
            inputs.new(MirroringObjectsSocket.bl_idname, "Mirroring")

    def update(self):
        pins = [p for p in self.inputs.values() if p.name.startswith('Constr')]

        for i in range(len(pins)-1, 0, -1):
            pins[i].enabled = pins[i].is_linked or pins[i-1].is_linked
            if (pins[i].enabled):
                break

    def isReady(self):
        name = self.inputs['Main Model'].getText()
        return True and name # force bool result

    def export(self, settings: ExportSettings):
        mainModel = self.inputs['Main Model']
        if not mainModel.is_linked:
            settings.error("not linked to a main model", node=self)
            return 'FAILED'

        name = mainModel.getText(settings)
        if not name:
            settings.error("main model has no name", node=self)
            return 'FAILED'

        blockdeffile = join(settings.outputDir, name + ".blockdef.xml")
        if blockdeffile in settings.cache:
            return settings.cache[blockdeffile]

        write_pretty_xml(self.generateBlockDefXml(settings), blockdeffile)
        settings.info("export successful", file=blockdeffile, node=self)
        return settings.cacheValue(blockdeffile, "SUCCESS")

    def generateBlockDefXml(self, settings: ExportSettings):
        mainModel = self.inputs['Main Model']
        if not mainModel.is_linked:
            raise ValueError("not linked to a main model")

        name = mainModel.getText(settings)
        if not name:
            raise ValueError("main model has no name")

        blockdeffile = join(settings.outputDir, name + ".blockdef.xml")
        blockdeffilecontent = blockdeffile + "|content"
        if blockdeffilecontent in settings.cache:
            return settings.cache[blockdeffilecontent]

        modelFile = name + ".mwm"

        mountPointsSocket = self.inputs['Mount Points']
        if mountPointsSocket.is_linked and mountPointsSocket.isEmpty():
            settings.text("no mount-points included", file=blockdeffile, node=self)

        mirroringSocket = self.inputs['Mirroring']

        constrModelFiles = [] # maybe stays empty
        for i, socket in enumerate(s for s in self.inputs if s.name.startswith('Constr')):
            if socket.enabled and socket.is_linked:
                constrName = socket.getText(settings)
                if socket.isReady():
                    constrModelFiles.append(constrName + ".mwm")
                else:
                    settings.text("socket '%s' not ready, skipped" % (socket.name), file=blockdeffile, node=self)

        mirrorSettings = settings.mirrorSettings()

        xml = generateBlockDefXml(
            settings,
            modelFile,
            mountPointsSocket.getObjects(),
            mirroringSocket.getObjects(),
            mirrorSettings.SubtypeId if mirrorSettings else None,
            constrModelFiles)

        return settings.cacheValue(blockdeffilecontent, xml)

    def getMainObjects(self):
         mwmMainFileSocket = self.inputs['Main Model'].firstSource(type=MwmFileSocket)
         if mwmMainFileSocket is None:
             raise ValueError('block-definition is not linked to a main model')

         mwmMainObjectsSocket = mwmMainFileSocket.node.inputs['Objects']
         return mwmMainObjectsSocket.getObjects()

    def _getLayer(self, socket: SESocket):
        source = socket.firstSource(type=ObjectsSocket)
        if source is None:
            return -1
        if not isinstance(source.node, LayerObjectsNode) and not isinstance(source.node):
            return -1
        return next( (i for i, b in enumerate(source.node.layer_mask) if b), -1)

    def getMountPointLayer(self):
        return self._getLayer(self.inputs['Mount Points'])

    def getMirroringLayer(self):
        return self._getLayer(self.inputs['Mirroring'])

# -------------------------------------------------------------------------------------------------------------------- #

def getBlockDef(nodeTree: bpy.types.NodeTree) -> BlockDefinitionNode:
     blockDef = first(n for n in nodeTree.nodes if isinstance(n, BlockDefinitionNode))
     if blockDef is None:
         raise ValueError('export settings contain no block-definition')
     return blockDef

def getUsedMaterials(scene: bpy.types.Scene = None):
    materials = set()

    scenes = [scene] if not scene is None else bpy.data.scenes
    for scene in scenes:
        with PinnedScene(scene):
            data = sceneData(scene)
            if not data or not data.is_block:
                continue

            try:
                exportTree = data.getExportNodeTree()
            except ValueError:
                continue

            for ob in exportTree.getAllMwmObjects():
                for slot in ob.material_slots:
                    if slot.material:
                        materials.add(slot.material)

    return materials

# -------------------------------------------------------------------------------------------------------------------- #

import nodeitems_utils
from nodeitems_utils import NodeCategory, NodeItem

class SENodeCategory(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == BlockExportTree.bl_idname

categories = [
    SENodeCategory(BlockExportTree.bl_idname, "Block Export", items=[
        NodeItem(LayerObjectsNode.bl_idname, LayerObjectsNode.bl_label),
        NodeItem(SeparateLayerObjectsNode.bl_idname, SeparateLayerObjectsNode.bl_label),
        NodeItem(TemplateStringNode.bl_idname, TemplateStringNode.bl_label),
        NodeItem(MwmFileNode.bl_idname, MwmFileNode.bl_label),
        NodeItem(HavokFileNode.bl_idname, HavokFileNode.bl_label),
        NodeItem(BlockDefinitionNode.bl_idname, BlockDefinitionNode.bl_label),
    ]),
]

registered = [
    BlockExportTree,

    MwmFileSocket,
    LodInputSocket,
    HktFileSocket,
    TemplateStringSocket,
    ObjectListSocket,
    RigidBodyObjectsSocket,
    MountPointObjectsSocket,
    MirroringObjectsSocket,

    LayerObjectsNode,
    SeparateLayerObjectsNode,
    HavokFileNode,
    MwmFileNode,
    TemplateStringNode,
    BlockDefinitionNode,
]

# -------------------------------------------------------------------------------------------------------------------- #

@bpy.app.handlers.persistent
def upgradeNodesAfterLoad(dummy):
    for nodeTree in bpy.data.node_groups:
        if isinstance(nodeTree, BlockExportTree):
            for node in nodeTree.nodes:
                if isinstance(node, Upgradable):
                    node.upgrade()

from bpy.utils import register_class, unregister_class

def register():
    for c in registered:
        register_class(c)

    if not upgradeNodesAfterLoad in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(upgradeNodesAfterLoad)

    try:
        nodeitems_utils.register_node_categories("SE_BLOCK_EXPORT", categories)
    except KeyError:
        nodeitems_utils.unregister_node_categories("SE_BLOCK_EXPORT")
        nodeitems_utils.register_node_categories("SE_BLOCK_EXPORT", categories)

def unregister():
    try:
        nodeitems_utils.unregister_node_categories("SE_BLOCK_EXPORT")
    except KeyError:
        pass

    if upgradeNodesAfterLoad in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(upgradeNodesAfterLoad)

    for c in reversed(registered):
        unregister_class(c)

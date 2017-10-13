from itertools import chain
import bpy
import re
import shutil
from os.path import join
from os import makedirs
from subprocess import CalledProcessError
from string import Template
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import path_reference_mode, orientation_helper_factory
from .mirroring import mirroringAxisFromObjectName
from .texture_files import TextureType
from .types import sceneData, data, SEMaterialInfo
from .utils import layer_bits, layer_bit, scene, first, PinnedScene, reportMessage, exportSettings
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

DEFAULT_OBJECT_TYPES = {'EMPTY', 'MESH'}
OTHER = 'OTHER'
OTHER_TYPES = {'OTHER'}
MESH_LIKE_TYPES = {'CURVE', 'SURFACE', 'FONT', 'META'}

ACCEPTABLE_OUTCOME = {'SUCCESS', 'PROBLEMS'}

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
    def upgrade(self, tree):
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
        return (o for o in super().getObjects(socket) if o.type == 'MESH' and not o.rigid_body is None)

class ExportableObjectsSocket(bpy.types.NodeSocket, ObjectsSocket):
    '''selects only objects that are of an exportable type'''
    bl_idname = "SEExportableObjectsSocket"
    bl_label = "Objects"
    bl_color = COLOR_OBJECTS_SKT
    type = 'CUSTOM'

    def getObjects(self, socket: bpy.types.NodeSocket=None):
        object_types = getattr(self.node, 'object_types', DEFAULT_OBJECT_TYPES)
        if OTHER in object_types:
            object_types = (object_types - OTHER_TYPES) | MESH_LIKE_TYPES

        return (o for o in super().getObjects(socket) if o.type in object_types)

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


IOFBXOrientationHelper = orientation_helper_factory("IOFBXOrientationHelper", axis_forward='Z', axis_up='Y') # SE; -Z, Y

# as defined by io_scene_fbx/__init__.py/ExportFBX, can't reuse because the class is a bpy.types.Operator
class FbxExportProperties (bpy.types.PropertyGroup, IOFBXOrientationHelper):
    version = EnumProperty(
        items=(('BIN7400', "FBX 7.4 binary", "Modern 7.4 binary version"),
               ('ASCII6100', "FBX 6.1 ASCII",
                "Legacy 6.1 ascii version - WARNING: Deprecated and no more maintained"),
               ),
        name="Version",
        description="Choose which version of the exporter to use",
        default='BIN7400' # SE
    )

    # 7.4 only
    ui_tab = EnumProperty(
        items=(('MAIN', "Main", "Main basic settings"),
               ('GEOMETRY', "Geometries", "Geometry-related settings"),
               ('ARMATURE', "Armatures", "Armature-related settings"),
               ('ANIMATION', "Animation", "Animation-related settings"),
               ),
        options={'SKIP_SAVE'}, # SE
        name="ui_tab",
        description="Export options categories",
    )

    use_selection = BoolProperty(
        name="Selected Objects",
        description="Export selected objects on visible layers",
        default=False,
    )

    global_scale = FloatProperty(
        name="Scale",
        description="Scale all data (Some importers do not support scaled armatures!)",
        min=0.001, max=1000.0,
        soft_min=0.01, soft_max=1000.0,
        default=1,
    )
    # 7.4 only
    apply_unit_scale = BoolProperty(
        name="Apply Unit",
        description="Scale all data according to current Blender size, to match default FBX unit "
                    "(centimeter, some importers do not handle UnitScaleFactor properly)",
        default=False, # SE ; True
    )
    # 7.4 only
    bake_space_transform = BoolProperty(
        name="!EXPERIMENTAL! Apply Transform",
        description="Bake space transform into object data, avoids getting unwanted rotations to objects when "
                    "target space is not aligned with Blender's space "
                    "(WARNING! experimental option, use at own risks, known broken with armatures/animations)",
        default=False,
    )

    object_types = EnumProperty(
        name="Object Types",
        options={'ENUM_FLAG'},
        items=(('EMPTY', "Empty", ""), # 'OUTLINER_OB_EMPTY'
               ('CAMERA', "Camera", ""), # 'OUTLINER_OB_CAMERA'
               ('LAMP', "Lamp", ""), # 'OUTLINER_OB_LAMP'
               ('ARMATURE', "Armature", "WARNING: not supported in dupli/group instances"), # 'OUTLINER_OB_ARMATURE'
               ('MESH', "Mesh", ""), # 'OUTLINER_OB_MESH'
               ('OTHER', "Other", "Other geometry types, like curve, metaball, etc. (converted to meshes)"), # 'MOD_REMESH'
               ),
        description="Which kind of object to export",
        default={'EMPTY', 'MESH' }, # SE ; {'EMPTY', 'CAMERA', 'LAMP', 'ARMATURE', 'MESH', 'OTHER' },
    )

    use_mesh_modifiers = BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers to mesh objects (except Armature ones) - "
                    "WARNING: prevents exporting shape keys",
        default=True,
    )
    mesh_smooth_type = EnumProperty(
        name="Smoothing",
        items=(('OFF', "Normals Only", "Export only normals instead of writing edge or face smoothing data"),
               ('FACE', "Face", "Write face smoothing"),
               ('EDGE', "Edge", "Write edge smoothing"),
               ),
        description="Export smoothing information "
                    "(prefer 'Normals Only' option if your target importer understand split normals)",
        default='OFF',
    )
    use_mesh_edges = BoolProperty(
        name="Loose Edges",
        description="Export loose edges (as two-vertices polygons)",
        default=False,
    )
    # 7.4 only
    use_tspace = BoolProperty(
        name="Tangent Space",
        description="Add binormal and tangent vectors, together with normal they form the tangent space "
                    "(will only work correctly with tris/quads only meshes!)",
        default=False,
    )
    # 7.4 only
    use_custom_props = BoolProperty(
        name="Custom Properties",
        description="Export custom properties",
        default=False,
    )
    add_leaf_bones = BoolProperty(
        name="Add Leaf Bones",
        description="Append a final bone to the end of each chain to specify last bone length "
                    "(use this when you intend to edit the armature from exported data)",
        default=False
    )
    primary_bone_axis = EnumProperty(
        name="Primary Bone Axis",
        items=(('X', "X Axis", ""),
               ('Y', "Y Axis", ""),
               ('Z', "Z Axis", ""),
               ('-X', "-X Axis", ""),
               ('-Y', "-Y Axis", ""),
               ('-Z', "-Z Axis", ""),
               ),
        default='X', # SE ; X
    )
    secondary_bone_axis = EnumProperty(
        name="Secondary Bone Axis",
        items=(('X', "X Axis", ""),
               ('Y', "Y Axis", ""),
               ('Z', "Z Axis", ""),
               ('-X', "-X Axis", ""),
               ('-Y', "-Y Axis", ""),
               ('-Z', "-Z Axis", ""),
               ),
        default='Y', # SE ; X
    )
    use_armature_deform_only = BoolProperty(
        name="Only Deform Bones",
        description="Only write deforming bones (and non-deforming ones when they have deforming children)",
        default=False,
    )
    armature_nodetype = EnumProperty(
        name="Armature FBXNode Type",
        items=(('NULL', "Null", "'Null' FBX node, similar to Blender's Empty (default)"),
               ('ROOT', "Root", "'Root' FBX node, supposed to be the root of chains of bones..."),
               ('LIMBNODE', "LimbNode", "'LimbNode' FBX node, a regular joint between two bones..."),
               ),
        description="FBX type of node (object) used to represent Blender's armatures "
                    "(use Null one unless you experience issues with other app, other choices may no import back "
                    "perfectly in Blender...)",
        default='NULL',
    )
    # Anim - 7.4 ;
    bake_anim = BoolProperty(
        name="Baked Animation",
        description="Export baked keyframe animation",
        default=False, # SE ; True
    )
    bake_anim_use_all_bones = BoolProperty(
        name="Key All Bones",
        description="Force exporting at least one key of animation for all bones "
                    "(needed with some target applications, like UE4)",
        default=True,
    )
    bake_anim_use_nla_strips = BoolProperty(
        name="NLA Strips",
        description="Export each non-muted NLA strip as a separated FBX's AnimStack, if any, "
                    "instead of global scene animation",
        default=True,
    )
    bake_anim_use_all_actions = BoolProperty(
        name="All Actions",
        description="Export each action as a separated FBX's AnimStack, instead of global scene animation "
                    "(note that animated objects will get all actions compatible with them, "
                    "others will get no animation at all)",
        default=True,
    )
    bake_anim_force_startend_keying = BoolProperty(
        name="Force Start/End Keying",
        description="Always add a keyframe at start and end of actions for animated channels",
        default=True,
    )
    bake_anim_step = FloatProperty(
        name="Sampling Rate",
        description="How often to evaluate animated values (in frames)",
        min=0.01, max=100.0,
        soft_min=0.1, soft_max=10.0,
        default=1.0,
    )
    bake_anim_simplify_factor = FloatProperty(
        name="Simplify",
        description="How much to simplify baked values (0.0 to disable, the higher the more simplified)",
        min=0.0, max=100.0,  # No simplification to up to 10% of current magnitude tolerance.
        soft_min=0.0, soft_max=10.0,
        default=1.0,  # default: min slope: 0.005, max frame step: 10.
    )
    # Anim - 6.1
    use_anim = BoolProperty(
        name="Animation",
        description="Export keyframe animation",
        default=False, # SE ; True
    )
    use_anim_action_all = BoolProperty(
        name="All Actions",
        description="Export all actions for armatures or just the currently selected action",
        default=True,
    )
    use_default_take = BoolProperty(
        name="Default Take",
        description="Export currently assigned object and armature animations into a default take from the scene "
                    "start/end frames",
        default=True
    )
    use_anim_optimize = BoolProperty(
        name="Optimize Keyframes",
        description="Remove double keyframes",
        default=True,
    )
    anim_optimize_precision = FloatProperty(
        name="Precision",
        description="Tolerance for comparing double keyframes (higher for greater accuracy)",
        min=0.0, max=20.0,  # from 10^2 to 10^-18 frames precision.
        soft_min=1.0, soft_max=16.0,
        default=6.0,  # default: 10^-4 frames.
    )
    # End anim
    path_mode = path_reference_mode
    # 7.4 only
    embed_textures = BoolProperty(
        name="Embed Textures",
        description="Embed textures in FBX binary file (only for \"Copy\" path mode!)",
        default=False,
    )
    batch_mode = EnumProperty(
        name="Batch Mode",
        items=(('OFF', "Off", "Active scene to file"),
               ('SCENE', "Scene", "Each scene as a file"),
               ('GROUP', "Group", "Each group as a file"),
               ),
        default = 'OFF' # SE
    )
    use_batch_own_dir = BoolProperty(
        name="Batch Own Dir",
        description="Create a dir for each exported file",
        default=True,
    )
    use_metadata = BoolProperty(
        name="Use Metadata",
        default=True,
        options={'HIDDEN'},
    )

class MwmExportProperties(bpy.types.PropertyGroup):
    rescale_factor = bpy.props.FloatProperty(name="Rescale Factor", min=0.001, max=1000, soft_min=0.01, soft_max=10, default=0.01,
        description="Instructs MwmBuilder to rescale everything by the given factor. Exporting a character seems to require a value 0.01. The armature must have the same scale.")
    rotation_y = bpy.props.FloatProperty(name="Rotation Y", min=-1000, max=1000, soft_min=-360, soft_max=360, default=0,
        description="Instructs MwmBuilder to rotate everything around the Y-axis. Exporting a character seems to require a value of 180°")

class MwmFileNode(bpy.types.Node, SENode, Exporter, ReadyState, Upgradable):
    bl_idname = "SEMwmFileNode"
    bl_label = "MwmBuilder"
    bl_icon = "EXPORT"

    fbx_settings = bpy.props.PointerProperty(type=FbxExportProperties)
    mwm_settings = bpy.props.PointerProperty(type=MwmExportProperties)

    @property
    def object_types(self):
        return self.fbx_settings.object_types

    def init(self, context):
        self.inputs.new(TemplateStringSocket.bl_idname, "Name")
        self.inputs.new(ExportableObjectsSocket.bl_idname, "Objects")
        self.inputs.new(HktFileSocket.bl_idname, "Havok")
        self.outputs.new(MwmFileSocket.bl_idname, "Mwm").node_input = "Name"

        for i in range(1,11):
            self.inputs.new(LodInputSocket.bl_idname, "LOD %d" % (i))

        self.use_custom_color = True
        self.color = COLOR_MWM_WND

    def upgrade(self, tree):
        # changed in 0.6.4
        if not isinstance(self.inputs["Objects"], ExportableObjectsSocket):
            oldSocket = self.inputs["Objects"]
            newSocket = self.inputs.new(ExportableObjectsSocket.bl_idname, "Objects")
            linkedSource = oldSocket.firstSource()
            if linkedSource:
                tree.links.new(linkedSource, newSocket)
            self.inputs.remove(oldSocket)
            self.inputs.move(len(self.inputs)-1, 1)

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

    def draw_buttons_ext(self, context, layout):
        f = self.fbx_settings


        m = self.mwm_settings
        layout.label("MwmBuilder Settings")
        layout.prop(m, 'rescale_factor')
        layout.prop(m, 'rotation_y')

        layout.separator()

        layout.label("FBX Exporter Settings")
        layout.prop(f, "ui_tab", expand=True)
        if f.ui_tab == 'MAIN':
            # layout.prop(f, "use_selection")
            row = layout.row(align=True)
            row.prop(f, "global_scale")
            sub = row.row(align=True)
            sub.prop(f, "apply_unit_scale", text="", icon='NDOF_TRANS')
            layout.prop(f, "axis_forward")
            layout.prop(f, "axis_up")

            layout.separator()
            layout.prop(f, "object_types", expand=True)
            layout.prop(f, "bake_space_transform")
            layout.prop(f, "use_custom_props")

        elif f.ui_tab == 'GEOMETRY':
            layout.prop(f, "use_mesh_modifiers")
            layout.prop(f, "mesh_smooth_type")
            layout.prop(f, "use_mesh_edges")
            sub = layout.row()
            #~ sub.enabled = f.mesh_smooth_type in {'OFF'}
            sub.prop(f, "use_tspace")
        elif f.ui_tab == 'ARMATURE':
            layout.prop(f, "use_armature_deform_only")
            layout.prop(f, "add_leaf_bones")
            layout.prop(f, "primary_bone_axis")
            layout.prop(f, "secondary_bone_axis")
            layout.prop(f, "armature_nodetype")
        elif f.ui_tab == 'ANIMATION':
            layout.prop(f, "bake_anim")
            col = layout.column()
            col.enabled = f.bake_anim
            col.prop(f, "bake_anim_use_all_bones")
            col.prop(f, "bake_anim_use_nla_strips")
            col.prop(f, "bake_anim_use_all_actions")
            col.prop(f, "bake_anim_force_startend_keying")
            col.prop(f, "bake_anim_step")
            col.prop(f, "bake_anim_simplify_factor")

    def export(self, settings: ExportSettings):
        _ = settings.hadErrors # reset error tracking

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

        sockets = [s for s in self.inputs if s.name.startswith("LOD") and s.enabled and s.is_linked]
        lods_xml = []
        msgs = []
        for i, socket in enumerate(sockets):
            lodName = socket.getText(settings)
            if socket.isReady() and socket.export(settings) in ACCEPTABLE_OUTCOME:
                lodDistance = socket.distance
                renderQualities = socket.qualities if socket.use_qualities else None
                lods_xml.append(lod_xml(settings, lodName, lodDistance, renderQualities))
            else:
                # report skips grouped after the export of dependencies
                msgs.append("socket '%s' not ready, skipped" % (socket.name))
        for msg in msgs:
            settings.text(msg, file=mwmfile, node=self)

        havokfile = None
        socket = self.inputs['Havok']
        if socket.isReady() and socket.export(settings) in ACCEPTABLE_OUTCOME:
            sourceName = socket.getText(settings)
            havokfile = join(settings.outputDir, sourceName + ".hkt")
        else:
            settings.info("no collision data included", file=mwmfile, node=self)

        materials = {}
        for o in objectsSource.getObjects():
            for ms in o.material_slots:
                if not ms is None and not ms.material is None:
                    materials[ms.material.name] = ms.material
            if isinstance(o.data, bpy.types.Mesh) and len(o.data.uv_layers) == 0:
                settings.error("Mesh-object '%s' has no UV-map. This will crash SE's DirectX 11 renderer." % o.name, file=mwmfile, node=self)
        materials_xml = [material_xml(settings, m, mwmfile, self) for m in materials.values()]

        paramsfile = join(settings.outputDir, name + ".xml")
        paramsxml = mwmbuilder_xml(settings, materials_xml, lods_xml, self.mwm_settings.rescale_factor, self.mwm_settings.rotation_y)
        write_pretty_xml(paramsxml, paramsfile)

        fbxfile = join(settings.outputDir, name + ".fbx")
        export_fbx(settings, fbxfile, objectsSource.getObjects(), self.fbx_settings)

        try:
            mwmbuilder(settings, fbxfile, havokfile, paramsfile, mwmfile)
        except CalledProcessError as e:
            settings.error(str(e), file=mwmfile, node=self)
            return settings.cacheValue(mwmfile, 'FAILED')

        if not settings.hadErrors:
            settings.info("export successful", file=mwmfile, node=self)
            return settings.cacheValue(mwmfile, 'SUCCESS')
        else:
            settings.warn("export completed with problems", file=mwmfile, node=self)
            return settings.cacheValue(mwmfile, 'PROBLEMS')

PATTERN_NAME = re.compile(r"^(.*?)(\.\d+)?$")

def object_basename(name: str) -> str:
    return PATTERN_NAME.match(name).group(1)

class TextFilterNode:
    def updateIsMalformedRegeEx(self, context):
        try:
            if (self.use_regex):
                re.compile(self.pattern)
            self.is_malformed_regex = ""
        except Exception as e:
            self.is_malformed_regex = str(e)

    pattern = bpy.props.StringProperty(
        name="Text Pattern",
        description="The text pattern to filter with",
        update=updateIsMalformedRegeEx)
    use_inverted_match = bpy.props.BoolProperty(
        name="Invert Match",
        description="Only keep objects that do *not* match the pattern?")
    use_regex = bpy.props.BoolProperty(
        name="Use Regular Expression", default=False,
        description="Is the text pattern a Python regular expression?",
        update=updateIsMalformedRegeEx)
    is_malformed_regex = bpy.props.StringProperty()
    use_case_sensitive = bpy.props.BoolProperty(
        name="Match Case Sensitively", default=False,
        description="Only match case-sensitively?")

    def getSearchSource(self):
        return None

    def drawPatternWidget(self, layout):
        if not self.use_regex:
            search_from = self.getSearchSource()
            if search_from:
                layout.prop_search(self, "pattern", search_from[0], search_from[1], text="")
                return
        layout.prop(self, "pattern", text="")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row2 = row.row(align=True)
        row2.alert = bool(self.is_malformed_regex)
        self.drawPatternWidget(row2)
        row.prop(self, "use_regex", text="", icon="SCRIPTPLUGINS")
        row2 = row.row(align=True)
        row2.enabled = self.use_regex
        row2.prop(self, "use_case_sensitive", text="", icon="FONT_DATA")
        invert_icon = "ZOOMOUT" if self.use_inverted_match else "ZOOMIN"
        row.prop(self, "use_inverted_match", text="", icon=invert_icon)
        if self.is_malformed_regex:
            layout.label(text=self.is_malformed_regex, icon="ERROR")

    def draw_buttons_ext(self, context, layout):
        row = layout.column()
        row.alert = bool(self.is_malformed_regex)
        self.drawPatternWidget(row)
        if self.is_malformed_regex:
            row.label(text=self.is_malformed_regex, icon="ERROR")
        else:
            row.label(text="")
        layout.prop(self, "use_regex")
        row = layout.row()
        row.enabled = self.use_regex
        row.prop(self, "use_case_sensitive")
        layout.prop(self, "use_inverted_match")

    def newMatcher(self):
        txt = self.pattern
        regex = None
        matcher = None
        def matchExact(text):
            return txt == text
        def matchAny(text):
            return True
        def matchNone(text):
            return False
        def matchRegEx(text):
            return bool(regex.search(text))
        def matchInverted(text):
            return not matcher(text)

        if not txt:
            matcher = matchAny
        elif self.use_regex:
            if self.is_malformed_regex:
                return matchNone
            else:
                regex = re.compile(self.pattern, 0 if self.use_case_sensitive else re.IGNORECASE)
                matcher = matchRegEx
        else:
            matcher = matchExact

        return matchInverted if self.use_inverted_match else matcher

class GroupFilterObjectsNode(bpy.types.Node, SENode, TextFilterNode, ObjectSource):
    bl_idname = "SEGroupFilterObjectsNode"
    bl_label = "Group Name Filter"
    bl_icon = "GROUP"
    bl_width_default = 170.0

    def init(self, context):
        pin = self.outputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def getObjects(self, socket: ObjectListSocket = None):
        inSocket = self.inputs['Objects']
        objects = inSocket.getObjects() if inSocket.is_linked else scene().objects
        matcher = self.newMatcher()
        return (obj for obj in objects
            if any(g for g in obj.users_group if matcher(g.name))
                or (self.use_inverted_match and len(obj.users_group) == 0))

    def getSearchSource(self):
        return (bpy.data, "groups")

class NameFilterObjectsNode(bpy.types.Node, SENode, TextFilterNode, ObjectSource):
    bl_idname = "SENameFilterObjectsNode"
    bl_label = "Object Name Filter"
    bl_icon = "COPY_ID"
    bl_width_default = 170.0

    def init(self, context):
        pin = self.outputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def getObjects(self, socket: ObjectListSocket = None):
        inSocket = self.inputs['Objects']
        objects = inSocket.getObjects() if inSocket.is_linked else scene().objects
        matcher = self.newMatcher()
        return (obj for obj in objects if matcher(obj.name))

    def getSearchSource(self):
        return (scene(), "objects")

class BlockSizeFilterObjectsNode(bpy.types.Node, SENode, ObjectSource):
    bl_idname = "SEBlockSizeFilterObjectsNode"
    bl_label = "Block Size Filter"
    bl_icon = "META_BALL"
    bl_width_default = 170.0

    def init(self, context):
        pin = self.outputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Large Block Objects")
        pin.n = -1
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Small Block Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def getObjects(self, socket: ObjectListSocket = None):
        settings = exportSettings()
        isSmall = (settings.CubeSize == 'Small') if settings else (data(scene()).block_size == 'SMALL')
        inSocket = self.inputs["Small Block Objects"] if isSmall else self.inputs["Large Block Objects"]
        return inSocket.getObjects() if inSocket.is_linked else scene().objects

class LayerObjectsNode(bpy.types.Node, SENode, ObjectSource, Upgradable):
    bl_idname = "SELayerObjectsNode"
    bl_label = "Layer Mask Filter"
    bl_icon = "GROUP"
    bl_width_default = 170.0

    layer_mask = bpy.props.BoolVectorProperty(name="Layers", subtype='LAYER', size=20, default=([False] * 20))

    def init(self, context):
        pin = self.outputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def upgrade(self, tree):
        inputs = self.inputs
        # new in 0.6.4
        if inputs.get('Objects', None) is None:
            pin = inputs.new(ObjectListSocket.bl_idname, "Objects")
            pin.n = -1

    def draw_buttons(self, context, layout):
        layout.prop(self, 'layer_mask')

    def getObjects(self, socket: ObjectListSocket):
        mask = layer_bits(self.layer_mask)
        inputSocket = self.inputs["Objects"]
        objects = inputSocket.getObjects() if inputSocket.is_linked else scene().objects
        return (obj for obj in objects if (layer_bits(obj.layers) & mask) != 0)

class SeparateLayerObjectsNode(bpy.types.Node, SENode, ObjectSource, Upgradable):
    bl_idname = "SESeparateLayerObjectsNode"
    bl_label = "Layer Splitter"
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
        pin = self.inputs.new(ObjectListSocket.bl_idname, "Objects")
        pin.n = -1
        self.use_custom_color = True
        self.color = COLOR_OBJECTS_WND

    def upgrade(self, tree):
        inputs = self.inputs
        # new in 0.6.4
        if inputs.get('Objects', None) is None:
            pin = inputs.new(ObjectListSocket.bl_idname, "Objects")
            pin.n = -1

    def draw_buttons(self, context, layout):
        layout.prop(self, 'layer_mask')

    def getObjects(self, socket: ObjectListSocket):
        mask = layer_bit(socket.layer)
        inputSocket = self.inputs["Objects"]
        objects = inputSocket.getObjects() if inputSocket.is_linked else scene().objects
        return (obj for obj in objects if (layer_bits(obj.layers) & mask) != 0)

class BlockDefinitionNode(bpy.types.Node, SENode, Exporter, ReadyState, Upgradable):
    bl_idname = "SEBlockDefNode"
    bl_label = "Block Definition"
    bl_icon = "SETTINGS"

    def init(self, context):
        inputs = self.inputs
        inputs.new(MwmFileSocket.bl_idname, "Main Model")
        icon = inputs.new(TemplateStringSocket.bl_idname, "Icon Path")
        icon.text = "//Textures/Icons/${BlockPairName}"
        inputs.new(MountPointObjectsSocket.bl_idname, "Mount Points")
        inputs.new(MirroringObjectsSocket.bl_idname, "Mirroring")

        for i in range(1,11):
            inputs.new(MwmFileSocket.bl_idname, "Constr. Phase %d" % (i))

        self.use_custom_color = True
        self.color = COLOR_BLOCKDEF_WND

    def upgrade(self, tree):
        inputs = self.inputs

        # new in v0.6.3
        if inputs.get('Icon Path', None) is None:
            icon = inputs.new(TemplateStringSocket.bl_idname, "Icon Path")
            icon.text = "//Textures/Icons/${BlockPairName}"
            inputs.move(len(inputs)-1, 1)

        # new in v0.5.0
        if inputs.get('Mirroring', None) is None:
            inputs.new(MirroringObjectsSocket.bl_idname, "Mirroring")
            inputs.move(len(inputs)-1, 3)

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

        iconPath = self.inputs['Icon Path'].getText(settings)
        iconFile = iconPath if iconPath else None

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
            iconFile,
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
    SENodeCategory(BlockExportTree.bl_idname+"Filters", "Object Filters", items=[
        NodeItem(NameFilterObjectsNode.bl_idname, NameFilterObjectsNode.bl_label),
        NodeItem(GroupFilterObjectsNode.bl_idname, GroupFilterObjectsNode.bl_label),
        NodeItem(BlockSizeFilterObjectsNode.bl_idname, BlockSizeFilterObjectsNode.bl_label),
        NodeItem(LayerObjectsNode.bl_idname, LayerObjectsNode.bl_label),
        NodeItem(SeparateLayerObjectsNode.bl_idname, SeparateLayerObjectsNode.bl_label),
    ]),
    SENodeCategory(BlockExportTree.bl_idname+"Exporters", "Block Export", items=[
        NodeItem(TemplateStringNode.bl_idname, TemplateStringNode.bl_label),
        NodeItem(MwmFileNode.bl_idname, MwmFileNode.bl_label),
        NodeItem(HavokFileNode.bl_idname, HavokFileNode.bl_label),
        NodeItem(BlockDefinitionNode.bl_idname, BlockDefinitionNode.bl_label),
    ]),
]

registered = [
    FbxExportProperties,
    MwmExportProperties,

    BlockExportTree,

    MwmFileSocket,
    LodInputSocket,
    HktFileSocket,
    TemplateStringSocket,
    ObjectListSocket,
    RigidBodyObjectsSocket,
    ExportableObjectsSocket,
    MountPointObjectsSocket,
    MirroringObjectsSocket,

    LayerObjectsNode,
    SeparateLayerObjectsNode,
    NameFilterObjectsNode,
    GroupFilterObjectsNode,
    BlockSizeFilterObjectsNode,
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
                    node.upgrade(nodeTree)


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

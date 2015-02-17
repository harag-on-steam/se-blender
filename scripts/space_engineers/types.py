import bpy
import os
from mathutils import Vector
from .utils import BoundingBox, layers, layer_bits, check_path
import requests
import re

PROP_GROUP = "space_engineers"

def data(obj):
    # avoids AttributeError
    return getattr(obj, PROP_GROUP, None)    

def some_layers_visible(layer_mask):
    scene_layers = layer_bits(bpy.context.scene.layers)
    mask = layer_bits(layer_mask)
    return (scene_layers & mask) != 0

def all_layers_visible(layer_mask):
    scene_layers = layer_bits(bpy.context.scene.layers)
    mask = layer_bits(layer_mask)
    return (scene_layers & mask) == mask

def getExportNodeTree(name):
    if name in bpy.data.node_groups:
        tree = bpy.data.node_groups[name]
        if tree.bl_idname == "SEBlockExportTree":
            return tree
    return None

def getExportNodeTreeFromContext(context):
    tree = None

    if context.space_data.type == 'NODE_EDITOR':
        tree = context.space_data.node_tree
    elif context.space_data.type == 'PROPERTIES':
        d = data(context.scene)
        if not d is None:
            settings = d.export_nodes
            if settings in bpy.data.node_groups:
                tree = bpy.data.node_groups[settings]

    if not tree is None and tree.bl_idname != "SEBlockExportTree":
        tree = None

    return tree

VERSION_URL = "https://api.github.com/repos/harag-on-steam/se-blender/tags"
online_version = None
is_current_version = None
version_pattern = re.compile(r"(\d+)\.(\d+)\.(\d+)")


# -----------------------------------------  Addon Data ----------------------------------------- #


class SEAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    seDir = bpy.props.StringProperty(
        name="Game Directory",
        subtype='DIR_PATH',
        description='The base directory of the game. Probably <Steam>\\SteamApps\\Common\\Space Engineers',
    )
    mwmbuilder = bpy.props.StringProperty(
        name="MWM Builder",
        subtype='FILE_PATH',
        description='Locate MwmBuilder.exe. Probably in <Game Directory>\\Tools\\MwmBuilder\\'
    )
    fix_dir_bug = bpy.props.BoolProperty(
        name="workaround for output-directory bug",
        description="Without the /o option mwmbuilder has been crashing since game version 01.059. "
                    "The option itself has a bug that outputs files in the wrong directory. "
                    "Only enable this for the broken version of mwmbuilder.",
    )

    havokFbxImporter = bpy.props.StringProperty(
        name="FBX Importer",
        subtype='FILE_PATH',
        description='Locate FBXImporter.exe',
    )
    havokFilterMgr = bpy.props.StringProperty(
        name="Standalone Filter Manager",
        subtype='FILE_PATH',
        description='Locate hctStandAloneFilterManager.exe. Probably in C:\\Program Files\\Havok\\HavokContentTools\\',
    )

    def draw(self, context):
        layout = self.layout

        col = layout.column()
        col.label(text="Space Engineers", icon="GAME")
        col.alert = not check_path(self.seDir, isDirectory=True, subpathExists='Bin64/SpaceEngineers.exe')
        col.prop(self, 'seDir')

        if not self.mwmbuilder and self.seDir:
            seDir = os.path.normpath(bpy.path.abspath(self.seDir))
            possibleMwmBuilder = os.path.join(seDir, 'Tools', 'MwmBuilder', 'MwmBuilder.exe')
            if (check_path(possibleMwmBuilder)):
                self.mwmbuilder = possibleMwmBuilder

        col.alert = not check_path(self.mwmbuilder, expectedBaseName='MwmBuilder.exe')
        col.prop(self, 'mwmbuilder')
        col.alert = False

        row = col.row()
        row.alignment = 'RIGHT'
        row.prop(self, 'fix_dir_bug')

        op = row.operator('wm.url_open', icon="URL", text="more details")
        op.url = 'http://forums.keenswh.com/post/?id=7197128&trail=18#post1285656779'

        col = layout.column()
        col.label(text="Havok Content Tools", icon="PHYSICS")
        col.alert = not check_path(self.havokFbxImporter, expectedBaseName='FBXImporter.exe')
        col.prop(self, 'havokFbxImporter')
        col.alert = not check_path(self.havokFilterMgr, expectedBaseName='hctStandAloneFilterManager.exe')
        col.prop(self, 'havokFilterMgr')
        col.alert = False

        layout.separator()

        row = layout.row()
        row.alignment = 'LEFT'
        if online_version:
            if is_current_version:
                from space_engineers import bl_info
                row.operator('wm.space_engineers_check_version', icon="URL")
                row.label(icon='FILE_TICK', text="%d.%d.%d is the current version" % bl_info['version'])
            else:
                op = row.operator('wm.url_open', icon="URL", text="Open GitHub Releases")
                op.url = 'https://github.com/harag-on-steam/se-blender/releases/latest'
                #op.url = 'http://forums.keenswh.com/post/?id=7090652&trail=81#81'
                row.label(icon='ERROR', text="There is a newer version %d.%d.%d" % online_version)
        else:
            row.operator('wm.space_engineers_check_version', icon="URL")
            row.label(icon='QUESTION', text="online version not known, yet")

def prefs() -> SEAddonPreferences:
    return bpy.context.user_preferences.addons[__package__].preferences

class CheckVersionOnline(bpy.types.Operator):
    bl_idname = "wm.space_engineers_check_version"
    bl_label = "Check version online"
    bl_description = "Retrieves the current version of this addon online and compares it against the running version."

    def execute(self, context):
        global online_version
        global is_current_version

        try:
            tags = requests.get(VERSION_URL, verify=False)
            json = tags.json()
        except (requests.RequestException, ValueError):
            self.report({'ERROR'}, "Failed to load version information from GitHub")
            return {'FINISHED'}

        for tag in json:
            match = version_pattern.match(tag['name'])
            if match:
                online_version = (
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
                break

        if not online_version:
            self.report({'WARN'}, "No version information available")
            return {'FINISHED'}

        is_current_version = True

        from space_engineers import bl_info

        for x, y in zip(bl_info['version'], online_version):
            if x < y:
                is_current_version = False
                break

        if is_current_version:
            self.report({'INFO'}, "%d.%d.%d is the current version" % bl_info['version'])
        else:
            self.report({'WARNING'}, "There is a newer version %d.%d.%d" % online_version)

        return {'FINISHED'}


# -----------------------------------------  Scene Data ----------------------------------------- #


BLOCK_SIZE = [
    ('LARGE', 'Large block only', 'Exports a large block. No attempt to export a small block is made for this scene.'),
    ('SCALE_DOWN', 'Large block and scale down', 'Exports a large and a small block. The small block is exported by scaling down the large block.'),
    ('SMALL', 'Small block only', 'Exports a small block. No attempt to export a large block is made for this scene.'),
]

class SESceneProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    
    is_block = bpy.props.BoolProperty( default=False, name="Export as Block", 
        description="Is this scene automatically exported to Space Engineers as a block according to the rules defined in this panel?")

    block_size =  bpy.props.EnumProperty( items=BLOCK_SIZE, default='SCALE_DOWN', name="Block Size")
    block_dimensions = bpy.props.IntVectorProperty( default=(1,1,1), min=1, description="Block Dimensions", subtype="TRANSLATION")

    block_specular_power = bpy.props.FloatProperty( min=0.0, description="per block specular power", )
    block_specular_shininess = bpy.props.FloatProperty( min=0.0, description="per block specular shininess", )

    main_layers =         bpy.props.BoolVectorProperty(subtype='LAYER', size=20, default=layers(0b10000000000000000000), 
                                name="Main Block", description="All meshes and empties on these layers will be part of the main block model.")
    physics_layers =      bpy.props.BoolVectorProperty(subtype='LAYER', size=20, default=layers(0b01000000000000000000), 
                                name="Collision", description="All meshes on these layers that have rigid bodies will contribute to the Havok collision model.")
    mount_points_layers = bpy.props.BoolVectorProperty(subtype='LAYER', size=20, default=layers(0b00100000000000000000), 
                                name="Mount Points", description="Meshes on these layers are searched for MountPoint polygons. "
                                                                 "Also, if one of these layers is visible the block-dimension box is shown.")
    construction_layers = bpy.props.BoolVectorProperty(subtype='LAYER', size=20, default=layers(0b00000000001110000000),
                                name="Construction Stages", description="Each layer in this set represents one construction stage. Only meshes and empties are included.")

    show_block_bounds = bpy.props.BoolProperty( default=True, name="Show Block Bounds", )

    use_custom_subtypeids = bpy.props.BoolProperty( default=False, name="Use custom SubtypeIds",
        description="This is only useful if you have to keep a specific block SubetypeId to remain backwards-compatible.")
    large_subtypeid = bpy.props.StringProperty( name="Large Block SubtypeId",
        description="Provide the SubtypeId of the large size block or leave empty to use the default naming-scheme")
    small_subtypeid = bpy.props.StringProperty( name="Small Block SubtypeId",
        description="Provide the SubtypeId of the small size block or leave empty to use the default naming-scheme")

    export_nodes = bpy.props.StringProperty( name="Export Node Tree", default="MwmExport",
        description="Use the Node editor to create and change these settings.")

class DATA_PT_spceng_scene(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"
    bl_label = "Space Engineers Block"

    @classmethod
    def poll(cls, context):
        return (context.scene and data(context.scene))

    def draw_header(self, context):
        self.layout.prop(data(context.scene), "is_block", text="")

    def draw(self, context):
        layout = self.layout
        spceng = data(context.scene)

        layout.active = spceng.is_block
        layout.enabled = spceng.is_block

        col = layout.column()
        col.label(text="Block Size")
        split = col.split(percentage=.45, align=True)
        split.prop(spceng, "block_size", text="")
        row = split.row(align=True)
        row.prop(spceng, "block_dimensions", text="")
        row.prop(spceng, "show_block_bounds", icon="MOD_MESHDEFORM", icon_only=True)

        row = layout.row()
        row.alignment = 'RIGHT'
        row.prop(spceng, 'use_custom_subtypeids')

        if spceng.use_custom_subtypeids:
            split = layout.split()

            col = split.column()
            col.enabled = spceng.block_size == 'LARGE' or spceng.block_size == 'SCALE_DOWN'
            col.label(text="Large SubtypeId")
            col.prop(spceng, 'large_subtypeid', text="")

            col = split.column()
            col.enabled =  spceng.block_size == 'SMALL' or spceng.block_size == 'SCALE_DOWN'
            col.label(text="Small SubtypeId")
            col.prop(spceng, 'small_subtypeid', text="")

        col = layout.column()
        col.label(text="Block Specular")
        split = col.split()
        split.column().prop(spceng, "block_specular_power", text="Power")
        split.column().prop(spceng, "block_specular_shininess", text="Shininess")

        layout.separator()
        layout.separator()

        col = layout.column(align=True)
        op = col.operator("export_scene.space_engineers_block", text="Export scene as a block", icon="EXPORT")
        op.settings_name = spceng.export_nodes
        op = col.operator("export_scene.space_engineers_update_definitions", text="Update block definitions", icon="FILE_REFRESH")
        op.settings_name = spceng.export_nodes
        layout.separator()

        row = layout.row(align=True)
        row.prop_search(spceng, "export_nodes", bpy.data, "node_groups", text="Export settings")
        if not any(nt for nt in bpy.data.node_groups if nt.bl_idname == "SEBlockExportTree"):
            row.operator("export_scene.space_engineers_export_nodes", text="", icon='ZOOMIN')

#        split = layout.split()
#        split.column().prop(spceng, "main_layers")
#        split.column().prop(spceng, "construction_layers")
        
#        split = layout.split()
#        split.column().prop(spceng, "physics_layers")
#        split.column().prop(spceng, "mount_points_layers")

class NODE_PT_spceng_nodes(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_label = "Space Engineers Export"

    @classmethod
    def poll(cls, context):
        return context.space_data.node_tree.bl_idname == "SEBlockExportTree"

    def draw(self, context):
        layout = self.layout

        layout.label("Export using these settings")
        col = layout.column(align=True)
        op = col.operator("export_scene.space_engineers_block", text="Export scene as a block", icon="EXPORT")
        op.settings_name = context.space_data.node_tree.name
        col.operator("export_scene.space_engineers_update_definitions", text="Update block definitions", icon="FILE_REFRESH")
        op.settings_name = context.space_data.node_tree.name

        layout.operator("export_scene.space_engineers_export_nodes", text="Add default export-nodes", icon='ZOOMIN')

def block_bounds():
    """
    The bounding-box of the scene's block.
    """
    scale = Vector((1.25, 1.25, 1.25))

    d = data(bpy.context.scene)
    if d:
        dim = d.block_dimensions
        scale = Vector((scale.x*dim[0], scale.y*dim[1], scale.x*dim[2]))
        if 'SMALL' == d.block_size:
            scale *= 0.2

    return BoundingBox(
        Vector((-scale.x, -scale.y, -scale.z)), #FBL
        Vector((-scale.x, -scale.y,  scale.z)), #FTL
        Vector((-scale.x,  scale.y,  scale.z)), #BTL
        Vector((-scale.x,  scale.y, -scale.z)), #BBL
        Vector(( scale.x, -scale.y, -scale.z)), #FBR
        Vector(( scale.x, -scale.y,  scale.z)), #FTR
        Vector(( scale.x,  scale.y,  scale.z)), #BTR
        Vector(( scale.x,  scale.y, -scale.z)), #BBR
    )

def is_small_block():
    d = data(bpy.context.scene)
    return d and 'SMALL' == d.block_size

def is_mount_points_visible():
    scene = bpy.context.scene
    d = data(scene)
    return d and d.is_block and d.show_block_bounds
         
# -----------------------------------------  Object Data ----------------------------------------- #
 
 
class SEObjectProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    file = bpy.props.StringProperty(name="Link to File", 
        description="Links this empty to another model file. Only specify the base name, do not include the .mwm extension.")

class DATA_PT_spceng_empty(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_label = "Space Engineers"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (ob and ob.type == 'EMPTY' and data(ob))

    def draw(self, context):
        d = data(context.active_object)

        layout = self.layout

        if context.active_object.name.lower().startswith("subpart_") and not d.file:
            layout.alert = True
        layout.prop(d, "file", text="Link to File", icon='LIBRARY_DATA_DIRECT')
        layout.alert = False

# -----------------------------------------  Material Data ----------------------------------------- #


MATERIAL_TECHNIQUES = [
    ('MESH', 'Normal Material', 'Normal, opaque material'),
    ('GLASS', 'Glass Material', 'The material references glass settings in TransparentMaterials.sbc'),
    # there is also an ALPHA_MASK technique, but no clue how that works
]

class SEMaterialProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    
    technique = bpy.props.EnumProperty(items=MATERIAL_TECHNIQUES, default='MESH', name="Technique")
    
    # the material might be a node material and have no diffuse color, so define our own
    diffuse_color = bpy.props.FloatVectorProperty( subtype="COLOR", default=(1.0, 1.0, 1.0), min=0.0, max=1.0, name="Diffuse Color", )
    specular_power = bpy.props.FloatProperty( min=0.0, name="Specular Power", description="per material specular power", )
    specular_intensity = bpy.props.FloatProperty( min=0.0, name="Specular Intensity", description="per material specular intensity", )
    
    glass_material_ccw = bpy.props.StringProperty(
        name="Outward Facing Material", 
        description="The material used on the side of the polygon that is facing away from the block center. Defined in TransparentMaterials.sbc",
    )
    glass_material_cw = bpy.props.StringProperty(
        name="Inward Facing Material", 
        description="The material used on the side of the polygon that is facing towards the block center. Defined in TransparentMaterials.sbc",
    )
    glass_smooth = bpy.props.BoolProperty(name="Smooth Glass", description="Should the faces of the glass be shaded smooth?")
    
    # texture paths are derived from the material textures

class DATA_PT_spceng_material(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_label = "Space Engineers"

    @classmethod
    def poll(cls, context):
        return (context.material and data(context.material))

    def draw(self, context):
        layout = self.layout

        mat = context.material
        d = data(mat)

        layout.prop(d, "technique")

        col = layout.column()

        # TODO decide if diffuse_color is needed or should always stay white
        # if 'MESH' == d.technique:
        #     split = col.split()
        #     split.column().prop(d, "diffuse_color")
        #     split.column()
            
        col.label(text="Specular")
        split = col.split()
        split.column().prop(d, "specular_intensity", text="Intensity")
        split.column().prop(d, "specular_power", text="Power")

        if 'GLASS' == d.technique:
            layout.separator()
            layout.prop(d, "glass_smooth")
            
            col = layout.column()
            # col.label(text="Glass settings")
            col.prop(d, "glass_material_ccw", icon='LIBRARY_DATA_DIRECT', text="Outwards")
            col.prop(d, "glass_material_cw", icon='LIBRARY_DATA_DIRECT', text="Inwards")

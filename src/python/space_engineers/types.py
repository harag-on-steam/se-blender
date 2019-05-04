from collections import OrderedDict
import re
import bpy
import os
import requests
import winreg
import hashlib as hash
from mathutils import Vector
from .mirroring import mirroringAxisFromObjectName
from .pbr_node_group import firstMatching, createMaterialNodeTree, createDx11ShaderGroup, getDx11Shader, \
    getDx11ShaderGroup
from .utils import data
from .texture_files import TextureType, textureFileNameFromPath,  \
    matchingFileNamesFromFilePath, imageFromFilePath, imageNodes
from .utils import BoundingBox, layers, layer_bits, check_path, scene
from . import addon_updater_ops

mwmbuilderEkHashSHA256 = "b8e978d7d9d229456b59786dd68d9a52c4e8665f10d93498dec75787ac0f3859"

PROP_GROUP = "space_engineers"  

def data(obj):
    # avoids AttributeError
    return getattr(obj, PROP_GROUP, None)

def some_layers_visible(layer_mask):
    scene_layers = layer_bits(scene().layers)
    mask = layer_bits(layer_mask)
    return (scene_layers & mask) != 0

def all_layers_visible(layer_mask):
    scene_layers = layer_bits(scene().layers)
    mask = layer_bits(layer_mask)
    return (scene_layers & mask) == mask

def getExportNodeTree(name):
    if name in bpy.data.node_groups:
        tree = bpy.data.node_groups[name]
        if not tree is None and tree.bl_idname == "SEBlockExportTree":
            return tree
    return None

def getExportNodeTreeFromContext(context):
    tree = None

    if context.space_data.type == 'NODE_EDITOR':
        tree = context.space_data.node_tree
    elif context.space_data.type in {'PROPERTIES', 'INFO'}:
        d = data(context.scene)
        if not d is None:
            settings = d.export_nodes
            if settings in bpy.data.node_groups:
                tree = bpy.data.node_groups[settings]

    if not tree is None and tree.bl_idname != "SEBlockExportTree":
        tree = None

    return tree

def getBaseDir(scene):
    # TODO make configurable
    return bpy.path.abspath('//')

# -----------------------------------------  Addon Data ----------------------------------------- #
    
def hashsha256(file):
    if check_path(file, expectedBaseName='MwmBuilder.exe'):
        
        sha = hash.sha256()
        with open(file,'rb') as checkfile:
            file_buffer = checkfile.read(65536)
            while len(file_buffer) > 0:
                sha.update(file_buffer)
                file_buffer = checkfile.read(65536)
        return sha.hexdigest()
    else:
        print("Hash check error: can't find %s." % file)
        return "not found"

class SEAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    
    mwmbuilderactual = bpy.props.StringProperty(
        name="",
        subtype='FILE_PATH',
        description='Locate actual MwmBuilder.exe.\nProbably in <Game Directory>\\Tools\\MwmBuilder\\'
    )
    mwmbuilder = bpy.props.StringProperty(
        name="Old/Custom MwmBuilder",
        subtype='FILE_PATH',
        description="Locate old or Custom one like Eikster's fixed Version of MwmBuilder.exe (Extra Download)"
    )
    mwmbuilderEkHash = bpy.props.StringProperty(
        name="Eikester MwmBuilder SHA256 Hash",
        default=mwmbuilderEkHashSHA256,
        description="SHA256 Hash to detect Eikester's MwmBuilder.exe - only change for new Versions.\nEmpty field reset it to default"
    )
    isEkmwmbuilder = bpy.props.BoolProperty(
        default=False
    )
    
    materialref = bpy.props.StringProperty(
        name="",
        subtype='DIR_PATH',
        description='Link to the external material reference folder (SE ModSDK "OriginalContent\\Materials").'
    )
    
    havokFbxImporter = bpy.props.StringProperty(
        name="FBX Importer",
        subtype='FILE_PATH',
        description='Locate FBXImporter.exe (Extra Download)'
    )
    havokFilterMgr = bpy.props.StringProperty(
        name="Standalone Filter Manager",
        subtype='FILE_PATH',
        description='Locate hctStandAloneFilterManager.exe.\nProbably in C:\\Program Files\\Havok\\HavokContentTools\\'
    )
    node_advanced_expanded = bpy.props.BoolProperty(
        name="Description",
        description="Description",
        default=True,
        options={'SKIP_SAVE'}
    )
    seDir = bpy.props.StringProperty(
        name="Converted SE Textures",
        subtype='DIR_PATH',
        description="Location of converted SE Textures, because Blender 2.7x can't load BC7 DDS files.\nShould be the base path where the \"Content\\Textures\\\" directory lies\nPath must looks like \"SpaceEngineers\\Content\\Textures\\\""
    )
    mwmbuildercmdarg = bpy.props.StringProperty(
        name="MwmBuilder Extra Cmdline Arguments",
        description="Read the MwmBuilder Description.\n"
                    "/s, /o, /m are already used, some may not work.\nUse it on your own Risk.\n"
                    "Standard is empty"
    )
    
    
    node_updater_expanded = bpy.props.BoolProperty(
        name="Description",
        description="Description",
        default=True
    )
    
    auto_check_update = bpy.props.BoolProperty(
    name = "Auto-check for Update",
    description = "If enabled, auto-check for updates using an interval",
    default = False,
    )

    updater_intrval_months = bpy.props.IntProperty(
        name='Months',
        description = "Number of months between checking for updates",
        default=0,
        min=0
    )
    updater_intrval_days = bpy.props.IntProperty(
        name='Days',
        description = "Number of days between checking for updates",
        default=7,
        min=0,
    )
    updater_intrval_hours = bpy.props.IntProperty(
        name='Hours',
        description = "Number of hours between checking for updates",
        default=0,
        min=0,
        max=23
    )
    updater_intrval_minutes = bpy.props.IntProperty(
        name='Minutes',
        description = "Number of minutes between checking for updates",
        default=0,
        min=0,
        max=59
    )

    def invoke(self, context):
        print("Test update")

    def draw(self, context):
        layout = self.layout

               
        col = layout.split(0.45)
        col.label(text="Space Engineers", icon="GAME")

        raw = col.split()
        keyval = None
        
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "SOFTWARE\\Valve\\Steam\\Apps\\326880") as key:
            keyval = winreg.QueryValueEx(key, 'Installed')
                
        
        if not keyval[0] == 1 and not os.path.isfile(self.materialref+'\materials.xml'):
            raw.enabled = True
            op = raw.operator('steam.url_open', icon="EXTERNAL_DATA", text="Install SE Mod SDK")
        else:
            raw.enabled = False
            op = raw.operator('steam.url_open', icon="FILE_TICK", text="SE Mod SDK is installed")
            
        op.url = 'steam://install/326880'

        col = col.split()
        col.enabled = True
        
        op = col.operator('steam.url_open', icon="GAME", text="Open Steam Tool Tab")
        op.url = 'steam://open/tools'
        
        col = layout.row(align=True)
        
        row = col.split(0.347)
        
        row.label(text="Actual MwmBuilder")
        row.alert = not check_path(self.mwmbuilderactual, expectedBaseName='MwmBuilder.exe')
        row.prop(self, 'mwmbuilderactual')
        row.alert = False
        row = col.row()
        row.operator('autosearch.mwmbuilder', icon="VIEWZOOM", text="")
        
        col = layout.row()
        
        col.alert = not check_path(self.mwmbuilder, expectedBaseName='MwmBuilder.exe')
        col.prop(self,'mwmbuilder')
        col.alert = False
        
        
        col = layout.column()
        col.separator()
        col.label(text="Material library", icon="MATERIAL")
        
        col = layout.row(align=True)
        
        row = col.split(0.347)
        
        row.label(text="SE Mod SDK Materials Folder")
        row.alert = not os.path.isfile(self.materialref+'\materials.xml')
        row.prop(self, 'materialref')
        row.alert=False
        row = col.row()
        row.operator('autosearch.matlibpath', icon="VIEWZOOM", text="")
        
        col = layout.split(0.333)
        col.label(text="Needed for MwmBuilder:",icon="NONE")
        raw = col.split()
        raw.enabled = False
        if os.path.isfile(self.materialref+'\materials.xml') and not os.path.isdir("C:\KeenSWH\Sandbox\MediaBuild\MEContent\Materials"):
            raw.enabled = True
            
        if not os.path.isdir("C:\KeenSWH\Sandbox\MediaBuild\MEContent\Materials"):
            self.isEkmwmbuilder = False
            raw.alert=True
            raw.operator('settings.createcmatfolder', text = '  Create "C:\KeenSWH\Sandbox\MediaBuild\MEContent\Materials" Junction Folder  ' , icon = 'ERROR')
        else:
            self.isEkmwmbuilder = False
            raw.operator('settings.createcmatfolder', text = '  Found: "C:\KeenSWH\Sandbox\MediaBuild\MEContent\Materials"  ' , icon = 'FILE_TICK')
                
        col.alert=False  
        
        col = layout.column()
        col.separator()
        col.label(text="Havok Content Tools", icon="PHYSICS")
        col.alert = not check_path(self.havokFbxImporter, expectedBaseName='FBXImporter.exe')
        col.prop(self, 'havokFbxImporter')
        col.alert = not check_path(self.havokFilterMgr, expectedBaseName='hctStandAloneFilterManager.exe')
        col.prop(self, 'havokFilterMgr')
        col.alert = False
        
        box = layout.row()
        box.prop(
            self, "node_advanced_expanded", text="Advanced Options",
            icon='DISCLOSURE_TRI_DOWN' if self.node_advanced_expanded
            else 'DISCLOSURE_TRI_RIGHT')
        
        if self.node_advanced_expanded:

            col = layout.box()
        
            if self.seDir.endswith("Content\\"):
                self.seDir = self.seDir[:len(self.seDir)-8]
            elif self.seDir.endswith("Content\\Textures\\"):
                self.seDir = self.seDir[:len(self.seDir)-17]
            col.alert = not check_path(self.seDir, isDirectory=True , subpathExists='Content\Textures')
            col.prop(self, 'seDir')
            col.alert = False
            col.prop(self, 'mwmbuildercmdarg')            
            if self.mwmbuilderEkHash.strip() is "":
                self.mwmbuilderEkHash = mwmbuilderEkHashSHA256
            col.prop(self, 'mwmbuilderEkHash')
            
        box = layout.row()
        box.prop(
            self, "node_updater_expanded", text="Updater Settings",
            icon='DISCLOSURE_TRI_DOWN' if self.node_updater_expanded
            else 'DISCLOSURE_TRI_RIGHT')
        
        if self.node_updater_expanded:
            addon_updater_ops.update_settings_ui(self,context)
        
def prefs() -> SEAddonPreferences:
    return bpy.context.user_preferences.addons[__package__].preferences

# -----------------------------------------  Scene Data ----------------------------------------- #


BLOCK_SIZE = [
    ('LARGE', 'Large block only', 'Exports a large block. No attempt to export a small block is made for this scene.'),
    ('SCALE_DOWN', 'Large block and scale down', 'Exports a large and a small block. The small block is exported by scaling down the large block.'),
    ('SMALL', 'Small block only', 'Exports a small block. No attempt to export a large block is made for this scene.'),
]

class SESceneProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    
    is_block = bpy.props.BoolProperty( default=False, name="Export as Block", 
        description="Does this scene contain the models for a block in Space Engineers?")

    block_size =  bpy.props.EnumProperty( items=BLOCK_SIZE, default='SCALE_DOWN', name="Block Size")
    block_dimensions = bpy.props.IntVectorProperty( default=(1,1,1), min=1, description="Block Dimensions", subtype="TRANSLATION")

    block_specular_power = bpy.props.FloatProperty( min=0.0, description="per block specular power", )
    block_specular_shininess = bpy.props.FloatProperty( min=0.0, description="per block specular shininess", )

    # legacy layer-masks, not visible in UI
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
    export_path = bpy.props.StringProperty( name="Models Subpath", default="//Models", subtype='DIR_PATH',
        description="The directory this block is to exported to")
    export_path_lods = bpy.props.StringProperty( name="LODs Subpath", default="//Models", subtype='DIR_PATH',
        description="The directory this blocks LODs are to exported to")
    
    useactualmwmbuilder = bpy.props.BoolProperty(default=True, name="Use actual MWMBuilder",
        description="If unchecked it use the older / Custom Version of MWMBuilder from the settings")

    mirroring_block = bpy.props.StringProperty( name="Mirroring Block", default="",
        description="The block that the game should switch to if this block is mirrored")

    # too bad https://developer.blender.org/D113 never made it into Blender
    def getExportNodeTree(self):
        if not self.export_nodes:
            raise ValueError('scene references no export node-tree')
        nodeTree = bpy.data.node_groups.get(self.export_nodes, None)
        if nodeTree is None:
            raise ValueError('scene references a non-existing export node-tree')
        return nodeTree

    def getMirroringBlock(self):
        if not self.mirroring_block:
            return None
        mirrorScene = bpy.data.scenes.get(self.mirroring_block, None)
        if mirrorScene is None:
            raise ValueError(
                "scene '%s' references a non-existing mirroring block '%s'" %
                (self.scene.name, self.mirroring_block))
        if mirrorScene == self.scene:
            return None
        return data(mirrorScene)

    @property
    def scene(self) -> bpy.types.Scene:
        return self.id_data

def sceneData(scene: bpy.types.Scene) -> SESceneProperties:
    return data(scene)

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
        spceng = sceneData(context.scene)

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
        layout.prop_search(spceng, "mirroring_block", bpy.data, "scenes", text="Mirroring Block")

        layout.separator()

        col = layout.column(align=True)
        col.prop(spceng, "export_path")
        
        if bpy.context.user_preferences.addons["space_engineers"].preferences.isEkmwmbuilder:
            col = layout.column(align=True)
            if spceng.useactualmwmbuilder:
                col.enabled = False
            else:
                col.enabled = True
            col.prop(spceng, "export_path_lods")

        row = layout.row(align=True)
        row.prop_search(spceng, "export_nodes", bpy.data, "node_groups", text="Export Settings")
        if not any(nt for nt in bpy.data.node_groups if nt.bl_idname == "SEBlockExportTree"):
            row.operator("export_scene.space_engineers_export_nodes", text="", icon='ZOOMIN')

        layout.separator()
        
        row = layout.row()
        row.prop(spceng,"useactualmwmbuilder")
        
        col = layout.column(align=True)
        op = col.operator("export_scene.space_engineers_block", text="Export scene as block", icon="EXPORT", )
        op.settings_name = spceng.export_nodes
        op = col.operator("export_scene.space_engineers_update_definitions", text="Update block definitions", icon="FILE_REFRESH")
        op.settings_name = spceng.export_nodes


class NODE_PT_spceng_nodes(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_label = "Space Engineers Export"

    @classmethod
    def poll(cls, context):
        nodeTree = getattr(context.space_data, 'node_tree')
        return nodeTree and nodeTree.bl_idname == "SEBlockExportTree"

    def draw(self, context):
        layout = self.layout

        layout.label("Export using these settings")
        col = layout.column(align=True)
        op = col.operator("export_scene.space_engineers_block", text="Export scene as a block", icon="EXPORT")
        op.settings_name = context.space_data.node_tree.name
        col.operator("export_scene.space_engineers_update_definitions", text="Update block definitions", icon="FILE_REFRESH")
        op.settings_name = context.space_data.node_tree.name

        col = layout.column(align=True)
        col.operator("export_scene.space_engineers_export_nodes", text="Add default export-nodes", icon='ZOOMIN')
        col.operator("object.space_engineers_layer_names", text="Set Layer Names", icon='COPY_ID')

class NODE_PT_spceng_nodes_mat(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_label = "Space Engineers Material"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'

    def draw(self, context):
        layout = self.layout
        layout.operator("material.spceng_material_setup", icon='NODETREE')

def block_bounds():
    """
    The bounding-box of the scene's block.
    """
    scale = Vector((1.25, 1.25, 1.25))

    d = data(scene())
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
    d = data(scene())
    return d and 'SMALL' == d.block_size

def show_block_bounds():
    d = data(scene())
    return d and d.is_block and d.show_block_bounds
         
# -----------------------------------------  Object Data ----------------------------------------- #
 
 
class SEObjectProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    file = bpy.props.StringProperty(name="Link to File", 
        description="Links this empty to another model file. Only specify the base name, do not include the .mwm extension.")
    # TODO SE supports referencing multiple highlight objects per empty. Which UI widget supports that in Blender?
    highlight_objects = bpy.props.StringProperty(name="Highlight Mesh",
        description="Link to a mesh-object that gets highlighted instead of this interaction handle "
                    "when the player points at the handle")
    scaleDown = bpy.props.BoolProperty(name="Scale Down", default=False,
        description="Should the empty be scaled down when exporting a small block from a large block model?")

_RE_KNOW_VOLUME_HANDLES = re.compile(r"^(dummy_)?(detector_(terminal|conveyor|cockpit))", re.IGNORECASE)

class DATA_PT_spceng_empty(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_label = "Space Engineers"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return (ob and ob.type == 'EMPTY' and data(ob))

    def draw(self, context):
        ob = context.object
        d = data(ob)
        isMirror = not mirroringAxisFromObjectName(context.active_object) is None

        layout = self.layout

        row = layout.row()
        row.enabled = not isMirror
        if context.object.name.lower().startswith("subpart_") and not d.file:
            row.alert = True
        row.prop(d, "file", text="Link to File", icon='LIBRARY_DATA_DIRECT')

        row = layout.row()
        row.enabled = isMirror
        row.prop(context.object, "space_engineers_mirroring", icon="MOD_MIRROR" if not isMirror else 'NONE')

        isVolumetric = ob.empty_draw_type == 'CUBE' and ob.empty_draw_size == 0.5

        row = layout.row()
        row.enabled = isVolumetric
        row.prop_search(d, "highlight_objects", context.scene, "objects", icon="FACESEL")

        row = layout.row()
        row.enabled = not isVolumetric
        row.prop(d, "scaleDown")

        if not isVolumetric:
            layout.separator()
            row = layout.row()
            row.alert = bool(_RE_KNOW_VOLUME_HANDLES.search(ob.name))
            row.operator('object.spceng_empty_with_volume', icon='BBOX')


# -----------------------------------------  Material Data ----------------------------------------- #


MATERIAL_TECHNIQUES = [
    ('MESH', 'Normal Material', 'Normal, opaque material'),
    ('GLASS', 'Glass Material', 'The material references glass settings in TransparentMaterials.sbc'),
    # 'ALPHAMASK' is missspelled. But it's already in use so fix it on export in .mwmbuilder._material_technique()
    ('ALPHAMASK', 'Alpha-Mask Material', 'The material uses a cut-off mask for completely transparent parts of the surface'),
    ('DECAL', 'Decal Material', 'The material uses a cut-off mask for completely transparent parts of the surface'),
    ('DECAL_CUTOUT', 'Decal Cutout Material', 'The material uses a cut-off mask for completely transparent parts of the surface'),
    ('DECAL_NOPREMULT', 'Decal NoPremult Material', 'The material uses a cut-off mask for completely transparent parts of the surface'),
    ('ALPHA_MASKED_SINGLE_SIDED', 'Alpha-Mask Single Sided Material', 'The material uses a cut-off mask for completely transparent parts of the surface'),
    ( 'AUTO', 'Auto Technique', 'Use the Technique from materials.xml material, if not found it will be MESH (= Normal Material)')
    # there are even more techniques, see VRage.Import.MyMeshDrawTechnique
]

def _texEnum(type, index, icon):
    return (type.name, type.name + "Texture", "", 1, icon)

# TODO maybe display these as enum_flags in the material panel
DX11_TEXTURE_SET = {TextureType.ColorMetal, TextureType.NormalGloss, TextureType.AddMaps, TextureType.Alphamask}
DX11_TEXTURE_ENUM = [
    _texEnum(TextureType.ColorMetal,  1, 'MATCAP_19'),
    _texEnum(TextureType.NormalGloss, 2, 'MATCAP_23'),
    _texEnum(TextureType.AddMaps,     3, 'MATCAP_09'),
    _texEnum(TextureType.Alphamask,   4, 'MATCAP_24'),
]

class SEMaterialProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
            
    nodes_version = bpy.props.IntProperty(default=0, options = {'SKIP_SAVE'})
    technique = bpy.props.EnumProperty(items=MATERIAL_TECHNIQUES, default='AUTO', name="Technique")
    usetextureng = bpy.props.BoolProperty(name='', description='If unchecked no NormalGloss Texture is saved in the XML for MWMBuilder', default = True)
    usetextureadd = bpy.props.BoolProperty(name='', description='If unchecked no AddMap Texture is saved in the XML for MWMBuilder', default = True)
    usetexturealpha = bpy.props.BoolProperty(name='', description='If unchecked no Alphamask Texture is saved in the XML for MWMBuilder', default = True)

class SEMaterialInfo:
    def __init__(self, material: bpy.types.Material):
        self.material = material

        if (material.node_tree): # the material might not have a node_tree, yet
            tree = material.node_tree
            nodes = material.node_tree.nodes
            self.textureNodes = imageNodes(nodes)
            self.altTextureNodes = imageNodes(nodes, alt=True)
            self.dx11Shader = getDx11ShaderGroup(tree)
            self.isnodemat = True
        else:
            self.textureNodes = {}
            self.altTextureNodes = {}
            self.dx11Shader = None
            self.isnodemat = False

        self.images = {t : n.image.filepath for t, n in self.textureNodes.items() if n.image and n.image.filepath}
        self.couldDefaultNormalTexture = False

        self.isOldMaterial = (len(self.textureNodes) == 0)
        if self.isOldMaterial:
            self._imagesFromLegacyMaterial()

        def val(n):
            return n.outputs[0].default_value

        d = data(self.material)

        alphamaskFilepath = self.images.get(TextureType.Alphamask, None)
        self.warnAlphaMask = bool(alphamaskFilepath and d.technique != 'ALPHAMASK' and d.technique != 'DECAL' and d.technique != 'DECAL_CUTOUT' and d.technique != 'DECAL_NOPREMULT' and d.technique != 'ALPHA_MASKED_SINGLE_SIDED')
        self.shouldUseNodes = not self.isOldMaterial and not material.use_nodes

    def _imagesFromLegacyMaterial(self):
        for slot in self.material.texture_slots:
            # getattr() because sometimes bpy.types.Texture has no attribute image (Blender bug?)
            if slot and getattr(slot, 'texture', None) and getattr(slot.texture, 'image', None):
                image = slot.texture.image
                filename = ''


def rgba(color: tuple, alpha=1.0):
    if len(color) == 4:
        return color
    r,g,b = color
    return (r,g,b,alpha)


def rgb(color: tuple):
    if len(color) == 3:
        return color
    r,g,b,_ = color
    return (r,g,b)

def upgradeToNodeMaterial(material: bpy.types.Material):
    # the material might not have a node_tree, yet
    if material.node_tree is None and not material.use_nodes:
        material.use_nodes = True
        material.use_nodes = False # retain the original setting in case the following raises an exception

    matInfoBefore = SEMaterialInfo(material)
    createMaterialNodeTree(material.node_tree)
    matInfo = SEMaterialInfo(material)

    imagesToSet = {k : imageFromFilePath(v) for k, v in matInfoBefore.images.items()}

    # for texType in [TextureType.ColorMetal, TextureType.Diffuse]:
    for texType in [TextureType.ColorMetal]:
        if texType in matInfoBefore.images:
            for mTexType, mTexFileName in matchingFileNamesFromFilePath(matInfoBefore.images[texType]).items():
                if not mTexType in imagesToSet:
                    imagesToSet[mTexType] = imageFromFilePath(mTexFileName.filepath)

    for texType, node in imageNodes(material.node_tree.nodes).items():
        if not node.image and texType in imagesToSet:
            node.image = imagesToSet[texType]

    material.use_nodes = True

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
        matInfo = SEMaterialInfo(mat)
        d = data(mat)

        def msg(msg, icon='INFO', layout=layout, align='CENTER'):
            row = layout.row()
            row.alignment = align
            row.label(msg, icon=icon)

        if not matInfo.isOldMaterial and context.scene.render.engine != 'CYCLES':
            msg("The render engine should be 'Cycles Render'.")
            layout.separator()

        splitPercent = 0.20

        col = layout.column()
        col.prop(d, "technique")
        
                

        if not 'GLASS' == d.technique:
            def image(texType: TextureType):
                if texType in matInfo.textureNodes:
                    split = layout.split(splitPercent)
                    split.label(texType.name)
                    split = split.row()
                    split.template_ID(matInfo.textureNodes[texType], 'image', open='image.open')
                    if texType.name == "NormalGloss":
                        split = split.row()
                        split.prop(d, 'usetextureng')
                    elif texType.name == "AddMaps":
                        split = split.row()
                        split.prop(d, 'usetextureadd')
                    elif texType.name == "Alphamask":
                        split = split.row()
                        split.prop(d, 'usetexturealpha')

            if matInfo.isOldMaterial:
                layout.separator()
                layout.operator("material.spceng_material_setup", "Convert to Nodes Material", icon="RECOVER_AUTO")
            else:
                layout.separator()
                layout.label('Texture Files (DirectX 11):')
                image(TextureType.ColorMetal)
                layout.separator()
                image(TextureType.NormalGloss)
                image(TextureType.AddMaps)
                if not 'MESH' == d.technique and not 'AUTO' == d.technique:
                    image(TextureType.Alphamask)
                if matInfo.shouldUseNodes:
                    layout.separator()
                    layout.operator("cycles.use_shading_nodes", icon="NODETREE")

@bpy.app.handlers.persistent
def syncTextureNodes(dummy):
    """
    This handler adresses https://github.com/harag-on-steam/se-blender/issues/6
    by syncing the image of <TextureType>Texture nodes with <TextureType>2Texture nodes.
    """
    for mat in bpy.data.materials:
        if mat.node_tree and mat.node_tree.is_updated:
            matInfo = SEMaterialInfo(mat)
            for t in TextureType:
                node = matInfo.textureNodes.get(t, None)
                altNode = matInfo.altTextureNodes.get(t, None)
                if not node is None and not altNode is None:
                    if node.image != altNode.image:
                        altNode.image = node.image

@bpy.app.handlers.persistent
def upgradeShadersAndMaterials(dummy):
    shaderTree = getDx11Shader(create=False)
    if shaderTree is None or len(shaderTree.inputs) == 14:
        return
    createDx11ShaderGroup() # recreate

def register():
    if not syncTextureNodes in bpy.app.handlers.scene_update_pre:
        bpy.app.handlers.scene_update_pre.append(syncTextureNodes)
    #if not upgradeShadersAndMaterials in bpy.app.handlers.load_post:
    #    bpy.app.handlers.load_post.append(upgradeShadersAndMaterials)

def unregister():
    if syncTextureNodes in bpy.app.handlers.scene_update_pre:
        bpy.app.handlers.scene_update_pre.remove(syncTextureNodes)
    #if upgradeShadersAndMaterials in bpy.app.handlers.load_post:
    #    bpy.app.handlers.load_post.remove(upgradeShadersAndMaterials)
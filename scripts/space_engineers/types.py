import bpy
from mathutils import Vector
from .utils import BoundingBox, layers, bitset


PROP_GROUP = "space_engineers"

def data(obj):
    # avoids AttributeError
    return getattr(obj, PROP_GROUP, None)    

def some_layers_visible(layer_mask):
    scene_layers = bitset(bpy.context.scene.layers)
    mask = bitset(layer_mask)
    return (scene_layers & mask) != 0

def all_layers_visible(layer_mask):
    scene_layers = bitset(bpy.context.scene.layers)
    mask = bitset(layer_mask)
    return (scene_layers & mask) == mask


# -----------------------------------------  Addon Data ----------------------------------------- #


class SEAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    seDir = bpy.props.StringProperty( name="Game Directory", subtype='DIR_PATH', )
    mwmbuilder = bpy.props.StringProperty( name="MWM Builder", subtype='FILE_PATH', )
    
    havokFbxImporter = bpy.props.StringProperty( name="FBX Importer", subtype='FILE_PATH', )
    havokFilterMgr = bpy.props.StringProperty( name="Standalone Filter Manager", subtype='FILE_PATH', )

    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.label(text="Space Engineers", icon="GAME")
        col.prop(self, 'seDir')
        col.prop(self, 'mwmbuilder')

        col = layout.column()
        col.label(text="Havok Content Tools", icon="PHYSICS")
        col.prop(self, 'havokFbxImporter')
        col.prop(self, 'havokFilterMgr')


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
                                name="Mount Points", description="")
    construction_layers = bpy.props.BoolVectorProperty(subtype='LAYER', size=20, default=layers(0b00000000001111100000), 
                                name="Construction Stages", description="Each layer in this set represents one construction stage. Only meshes and empties are included.")

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

        split = layout.split()
        col = split.column()
        col.label(text="Block Size")
        col.prop(spceng, "block_size", text="")

        col = split.column()
        col.label()
        col.row().prop(spceng, "block_dimensions", text="")

        layout.separator()

        col = layout.column()
        col.label(text="Block Specular")
        split = col.split()
        split.column().prop(spceng, "block_specular_power", text="Power")
        split.column().prop(spceng, "block_specular_shininess", text="Shininess")
        
        layout.separator()        
        layout.operator("wm.splash", text="Export block", icon="EXPORT") # TODO
        layout.separator()

        split = layout.split()
        split.column().prop(spceng, "main_layers")
        split.column().prop(spceng, "construction_layers")
        
        split = layout.split()
        split.column().prop(spceng, "physics_layers")
        split.column().prop(spceng, "mount_points_layers")

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
    return d and d.is_block and some_layers_visible(d.mount_points_layers)

         
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
        return (context.object and context.object.type == 'EMPTY')

    def draw(self, context):
        layout = self.layout

        spceng = getattr(context.active_object, PROP_GROUP, None)
        if not spceng: return
        
#        layout.prop_search(spceng, "file", bpy.data, "scenes", text="Link to File", icon='LIBRARY_DATA_DIRECT')
        layout.prop(spceng, "file", text="Link to File", icon='LIBRARY_DATA_DIRECT')


# -----------------------------------------  Material Data ----------------------------------------- #


MATERIAL_TECHNIQUES = [
    ('MESH', 'Normal Material', 'Normal, opaque material'),
    ('GLASS', 'Glass Material', 'The material references glass settings in TransparentMaterials.sbc'),
    # there is also an ALPHA_MASK technique, but no clue how that works
]

class SEMaterialProperties(bpy.types.PropertyGroup):
    name = PROP_GROUP
    
    technique = bpy.props.EnumProperty(items=MATERIAL_TECHNIQUES, default='MESH', name="Technique")
    
    # the material might be a node material and have no diffuse color
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
        return (context.material)

    def draw(self, context):
        layout = self.layout

        spceng = getattr(context.material, PROP_GROUP, None)
        if not spceng: return

        layout.prop(spceng, "technique")

        col = layout.column()
        if 'MESH' == spceng.technique:
            split = col.split()
            split.column().prop(spceng, "diffuse_color")
            split.column()
            
        col.label(text="Specular")
        split = col.split()
        split.column().prop(spceng, "specular_intensity", text="Intensity")
        split.column().prop(spceng, "specular_power", text="Power")

        if 'GLASS' == spceng.technique:
            layout.separator()
            layout.prop(spceng, "glass_smooth")
            
            col = layout.column()
            # col.label(text="Glass settings")
            col.prop(spceng, "glass_material_ccw", icon='LIBRARY_DATA_DIRECT', text="Outwards")
            col.prop(spceng, "glass_material_cw", icon='LIBRARY_DATA_DIRECT', text="Inwards")

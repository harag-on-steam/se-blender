bl_info = {
    "name": "Block Tools",
	"description": "Tools to construct in-game blocks for the game Space Engineers",
	"author": "Harag",
	"version": (0, 7, 0),
    "blender": (2, 72, 0),
	"location": "Properties > Scene, Material, Empty | Tools > Create | Node Editor",
	"wiki_url": "http://harag-on-steam.github.io/se-blender/",
	"tracker_url": "https://github.com/harag-on-steam/se-blender/issues",
    "category": "Space Engineers",
}

# properly handle Blender F8 reload

modules = locals()

def reload(module_name):
    import importlib
    try:
        importlib.reload(modules[module_name])
        return True
    except KeyError:
        return False

if not reload('utils'): from . import utils
if not reload('mirroring'): from . import mirroring
if not reload('texture_files'): from . import texture_files
if not reload('pbr_node_group'): from . import pbr_node_group
if not reload('types'): from . import types
if not reload('mount_points'): from . import mount_points
if not reload('mwmbuilder'): from . import mwmbuilder
if not reload('fbx'): from . import fbx
if not reload('havok_options'): from . import havok_options
if not reload('merge_xml'): from . import merge_xml
if not reload('export'): from . import export
if not reload('nodes'): from . import nodes
if not reload('default_nodes'): from . import nodes
if not reload('operators'): from . import operators
if not reload('versions'): from . import versions

del modules

version = versions.Version(version=bl_info['version'], prerelease=False, qualifier=None)

# register data & UI classes

import bpy

class SEView3DToolsPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS'
    bl_category = "Create"
    bl_context = "objectmode"
    bl_label = "Space Engineers"

    @classmethod
    def poll(self, context):
        return True

    def draw(self, context):
        layout = self.layout

        data = types.sceneData(context.scene)

        col = layout.column(align=True)
        col.enabled = data.is_block
        space = context.space_data
        if space.grid_scale != 1.25 or space.grid_subdivisions != 5:
            col.operator(operators.SetupGrid.bl_idname, icon='GRID')

        col.operator(operators.AddMountPointSkeleton.bl_idname, icon='FACESEL')
        col.operator(operators.AddMirroringEmpties.bl_idname, icon='MOD_MIRROR')

        if not data.is_block:
            col.separator()
            row = col.row()
            row.alignment = 'CENTER'
            row.label("Mark the scene as a block.", icon="INFO")

def menu_func_export(self, context):
    self.layout.operator(operators.ExportSceneAsBlock.bl_idname,
                         text="Space Engineers Block (.mwm)")
    self.layout.operator(operators.UpdateDefinitionsFromBlockScene.bl_idname,
                         text="Space Engineers Definition Update (.sbc)")

def register():
    from bpy.utils import register_class

    register_class(utils.MessageOperator)

    register_class(types.SEAddonPreferences)
    register_class(types.SESceneProperties)
    register_class(types.SEObjectProperties)
    register_class(types.SEMaterialProperties)
   
    bpy.types.Object.space_engineers = bpy.props.PointerProperty(type=types.SEObjectProperties)
    bpy.types.Object.space_engineers_mirroring = mirroring.mirroringProperty
    bpy.types.Scene.space_engineers = bpy.props.PointerProperty(type=types.SESceneProperties)
    bpy.types.Material.space_engineers = bpy.props.PointerProperty(type=types.SEMaterialProperties)

    register_class(types.NODE_PT_spceng_nodes)
    register_class(types.NODE_PT_spceng_nodes_mat)
    register_class(types.DATA_PT_spceng_scene)
    register_class(types.DATA_PT_spceng_empty)
    register_class(types.DATA_PT_spceng_material)

    types.register()
    pbr_node_group.register()

    register_class(types.CheckVersionOnline)
    operators.register()

    bpy.types.INFO_MT_file_export.append(menu_func_export)

    nodes.register()

    register_class(SEView3DToolsPanel)

    mount_points.enable_draw_callback()


def unregister():
    from bpy.utils import unregister_class

    mount_points.disable_draw_callback()

    unregister_class(SEView3DToolsPanel)

    nodes.unregister()

    bpy.types.INFO_MT_file_export.remove(menu_func_export)

    operators.unregister()
    unregister_class(types.CheckVersionOnline)

    pbr_node_group.unregister()
    types.unregister()

    unregister_class(types.DATA_PT_spceng_material)
    unregister_class(types.DATA_PT_spceng_empty)
    unregister_class(types.DATA_PT_spceng_scene)
    unregister_class(types.NODE_PT_spceng_nodes_mat)
    unregister_class(types.NODE_PT_spceng_nodes)

    del bpy.types.Material.space_engineers
    del bpy.types.Object.space_engineers
    del bpy.types.Object.space_engineers_mirroring
    del bpy.types.Scene.space_engineers
    
    unregister_class(types.SEMaterialProperties)
    unregister_class(types.SEObjectProperties)
    unregister_class(types.SESceneProperties)
    unregister_class(types.SEAddonPreferences)

    unregister_class(utils.MessageOperator)


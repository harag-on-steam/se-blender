bl_info = {
    "name": "Block Tools",
	"description": "Tools to construct in-game blocks for the game Space Engineers",
	"author": "Harag",
	"version": (0, 4, 1),
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

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)

        space = context.space_data
        if space.grid_scale != 1.25 or space.grid_subdivisions != 5:
            col.operator(mount_points.SetupGrid.bl_idname, icon='GRID')

        col.operator(mount_points.AddMountPointSkeleton.bl_idname, icon='FACESEL')

def register():
    from bpy.utils import register_class
    
    register_class(types.SEAddonPreferences)
    register_class(types.SESceneProperties)
    register_class(types.SEObjectProperties)
    register_class(types.SEMaterialProperties)
   
    bpy.types.Object.space_engineers = bpy.props.PointerProperty(type=types.SEObjectProperties)
    bpy.types.Scene.space_engineers = bpy.props.PointerProperty(type=types.SESceneProperties)
    bpy.types.Material.space_engineers = bpy.props.PointerProperty(type=types.SEMaterialProperties)
   
    register_class(types.NODE_PT_spceng_nodes)
    register_class(types.DATA_PT_spceng_scene)
    register_class(types.DATA_PT_spceng_empty)
    register_class(types.DATA_PT_spceng_material)

    register_class(operators.AddDefaultExportNodes)
    register_class(operators.ExportSceneAsBlock)
    register_class(operators.UpdateDefinitionsFromBlockScene)
    register_class(types.CheckVersionOnline)
    register_class(mount_points.AddMountPointSkeleton)
    register_class(mount_points.SetupGrid)

    nodes.register()

    register_class(SEView3DToolsPanel)

    mount_points.enable_draw_callback()


def unregister():
    from bpy.utils import unregister_class

    mount_points.disable_draw_callback()

    unregister_class(SEView3DToolsPanel)

    nodes.unregister()

    unregister_class(mount_points.SetupGrid)
    unregister_class(mount_points.AddMountPointSkeleton)
    unregister_class(types.CheckVersionOnline)
    unregister_class(operators.UpdateDefinitionsFromBlockScene)
    unregister_class(operators.ExportSceneAsBlock)
    unregister_class(operators.AddDefaultExportNodes)

    unregister_class(types.DATA_PT_spceng_material)
    unregister_class(types.DATA_PT_spceng_empty)
    unregister_class(types.DATA_PT_spceng_scene)
    unregister_class(types.NODE_PT_spceng_nodes)
    
    del bpy.types.Material.space_engineers
    del bpy.types.Object.space_engineers
    del bpy.types.Scene.space_engineers
    
    unregister_class(types.SEMaterialProperties)
    unregister_class(types.SEObjectProperties)
    unregister_class(types.SESceneProperties)
    unregister_class(types.SEAddonPreferences)


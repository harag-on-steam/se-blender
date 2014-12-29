bl_info = {
    "name": "Test Addon",
	"description": "Tools to construcht in-game blocks for the game Space Engineers",
	"author": "Harag",
	"version": (0, 1, 0),
	"location": "Properties > Scene / Material / Empty",
	"wiki_url": "https://github.com/harag-on-steam/se-blender/wiki",
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

if not reload('types'): from . import types
if not reload('fbx'): from . import fbx

del modules

# register data & UI classes

import bpy

class TestOperator(bpy.types.Operator):
    bl_idname = 'object.testmodule' 
    bl_label = 'Test: Export current scene to .fbx'
    bl_options = {'REGISTER' }
    
    def execute(self, context):
        import os
        import tempfile
        
        print(tempfile.gettempdir())
        testfile = os.path.join(tempfile.gettempdir(), 'test.fbx')
        
        fbx.save_single(
            self, 
            context.scene, 
            filepath=testfile, 
            context_objects = context.scene.objects, #context.selected_objects,
            object_types = {'EMPTY', 'MESH' },
        )
        
        self.report({"INFO"}, "Exported scene to %s" % (testfile))
        
        return {"FINISHED" }
    
class TestOperator2(bpy.types.Operator):
    bl_idname = 'object.mount_point_mesh' 
    bl_label = 'Test: Export current scene to .fbx'
    bl_options = {'REGISTER' }

    def execute(self, context):
        from mathutils import Matrix, Vector
        
        return {"FINISHED" }
    

def register():
    bpy.utils.register_class(types.SEAddonPreferences)
    bpy.utils.register_class(types.SESceneProperties)
    bpy.utils.register_class(types.SEObjectProperties)
    bpy.utils.register_class(types.SEMaterialProperties)
   
    bpy.types.Object.space_engineers = bpy.props.PointerProperty(type=types.SEObjectProperties)
    bpy.types.Scene.space_engineers = bpy.props.PointerProperty(type=types.SESceneProperties)
    bpy.types.Material.space_engineers = bpy.props.PointerProperty(type=types.SEMaterialProperties)
   
    bpy.utils.register_class(types.DATA_PT_spceng_scene)
    bpy.utils.register_class(types.DATA_PT_spceng_empty)
    bpy.utils.register_class(types.DATA_PT_spceng_material)

    bpy.utils.register_class(TestOperator)

def unregister():
    bpy.utils.unregister_class(TestOperator)

    bpy.utils.unregister_class(types.DATA_PT_spceng_material)
    bpy.utils.unregister_class(types.DATA_PT_spceng_empty)
    bpy.utils.unregister_class(types.DATA_PT_spceng_scene)
    
    del bpy.types.Material.space_engineers
    del bpy.types.Object.space_engineers
    del bpy.types.Scene.space_engineers
    
    bpy.utils.unregister_class(types.SEMaterialProperties)
    bpy.utils.unregister_class(types.SEObjectProperties)
    bpy.utils.unregister_class(types.SESceneProperties)
    bpy.utils.unregister_class(types.SEAddonPreferences)

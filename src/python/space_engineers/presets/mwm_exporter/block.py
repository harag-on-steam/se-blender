import bpy
node = bpy.context.active_node

node.mwm_settings.name = ''
node.mwm_settings.rescale_factor = 1.0
node.mwm_settings.rotation_y = 0.0

node.fbx_settings.name = ''
node.fbx_settings.version = 'BIN7400'
# node.fbx_settings.ui_tab = 'MAIN'

# main
node.fbx_settings.use_selection = False
node.fbx_settings.global_scale = 1.0
node.fbx_settings.apply_unit_scale = False
node.fbx_settings.axis_forward = 'Z'
node.fbx_settings.axis_up = 'Y'
node.fbx_settings.object_types = {'EMPTY', 'MESH'}
node.fbx_settings.bake_space_transform = False
node.fbx_settings.use_custom_props = False
node.fbx_settings.use_metadata = True
node.fbx_settings.path_mode = 'AUTO'
node.fbx_settings.embed_textures = False
node.fbx_settings.batch_mode = 'OFF'
node.fbx_settings.use_batch_own_dir = True

# geometry
node.fbx_settings.use_mesh_modifiers = True
node.fbx_settings.use_mesh_edges = False
node.fbx_settings.mesh_smooth_type = 'OFF'
node.fbx_settings.use_tspace = False

# armature
node.fbx_settings.use_armature_deform_only = False
node.fbx_settings.add_leaf_bones = False
node.fbx_settings.primary_bone_axis = 'X'
node.fbx_settings.secondary_bone_axis = 'Y'
node.fbx_settings.armature_nodetype = 'NULL'

# BIN7400 animation
node.fbx_settings.bake_anim = False
node.fbx_settings.bake_anim_use_all_bones = False
node.fbx_settings.bake_anim_force_startend_keying = False
node.fbx_settings.bake_anim_use_nla_strips = False
node.fbx_settings.bake_anim_use_all_actions = False
node.fbx_settings.bake_anim_simplify_factor = 1.0
node.fbx_settings.bake_anim_step = 1.0

# ASCII6100 animation
node.fbx_settings.use_anim = False
node.fbx_settings.use_anim_action_all = True
node.fbx_settings.use_anim_optimize = True
node.fbx_settings.use_default_take = True
node.fbx_settings.anim_optimize_precision = 6.0

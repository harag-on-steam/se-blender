from collections import OrderedDict
from . import types

def _clone_fbx_module():
    import sys
    import importlib
    NAME = 'io_scene_fbx_experimental.export_fbx_bin'
    saved_module = sys.modules.pop(NAME, None)
    try:
        spec = importlib.util.find_spec(NAME)
        return spec.loader.load_module()
    finally:
        if saved_module:
            sys.modules[NAME] = saved_module
        else:
            del sys.modules[NAME]

_fbx = _clone_fbx_module()

_original_fbx_template_def_model = _fbx.fbx_template_def_model

# extend fbx_template_def_model with further known properties by using the overrides
def fbx_template_def_model(scene, settings, override_defaults=None, nbr_users=0):
    props = OrderedDict((
        # Name,   Value, Type, Animatable
        
        # SE properties
        (b"file", ("", "p_string", False)),
        
        # Havok properties last to avoid including unrelated properties in the conversion to .hkt
        (b"hkTypeRigidBody", ("", "p_string", False)),
        (b"mass", (-1.0, "p_double", False)),
        (b"friction", (-1.0, "p_double", False)),
        (b"restitution", (-1.0, "p_double", False)),
        (b"hkTypeShape", ("", "p_string", False)),
        (b"shapeType", ("", "p_string", False)),
    ))
    if override_defaults is not None:
        props.update(override_defaults)        
    return _original_fbx_template_def_model(scene, settings, props, nbr_users)

_fbx.fbx_template_def_model = fbx_template_def_model

HAVOK_SHAPE_NAMES = {
    'CONVEX_HULL': 'Hull',
    'BOX': 'Box',
    'SPHERE': 'Sphere',
    'CYLINDER': 'Cylinder',
    'CAPSULE': 'Capsule',
    'MESH': 'Mesh',
    'CONE': 'Hull', # not supported by Havok
}

# no easy way to extend, so copied from export_fbx_bin.py and modified
def fbx_data_object_elements(root, ob_obj, scene_data):
    """
    Write the Object (Model) data blocks.
    Note this "Model" can also be bone or dupli!
    """
    obj_type = b"Null"  # default, sort of empty...
    if ob_obj.is_bone:
        obj_type = b"LimbNode"
    elif (ob_obj.type in _fbx.BLENDER_OBJECT_TYPES_MESHLIKE):
        obj_type = b"Mesh"
    elif (ob_obj.type == 'LAMP'):
        obj_type = b"Light"
    elif (ob_obj.type == 'CAMERA'):
        obj_type = b"Camera"
    model = _fbx.elem_data_single_int64(root, b"Model", ob_obj.fbx_uuid)
    model.add_string(_fbx.fbx_name_class(ob_obj.name.encode(), b"Model"))
    model.add_string(obj_type)

    _fbx.elem_data_single_int32(model, b"Version", _fbx.FBX_MODELS_VERSION)

    # Object transform info.
    loc, rot, scale, matrix, matrix_rot = ob_obj.fbx_object_tx(scene_data)
    rot = tuple(_fbx.convert_rad_to_deg_iter(rot))

    tmpl = _fbx.elem_props_template_init(scene_data.templates, b"Model")
    # For now add only loc/rot/scale...
    props = _fbx.elem_properties(model)
    _fbx.elem_props_template_set(tmpl, props, "p_lcl_translation", b"Lcl Translation", loc)
    _fbx.elem_props_template_set(tmpl, props, "p_lcl_rotation", b"Lcl Rotation", rot)
    _fbx.elem_props_template_set(tmpl, props, "p_lcl_scaling", b"Lcl Scaling", scale)
    _fbx.elem_props_template_set(tmpl, props, "p_visibility", b"Visibility", float(not ob_obj.hide))

    # Absolutely no idea what this is, but seems mandatory for validity of the file, and defaults to
    # invalid -1 value...
    _fbx.elem_props_template_set(tmpl, props, "p_integer", b"DefaultAttributeIndex", 0)

    _fbx.elem_props_template_set(tmpl, props, "p_enum", b"InheritType", 1)  # RSrs

    # Custom properties.
    if scene_data.settings.use_custom_props:
        _fbx.fbx_data_element_custom_properties(props, ob_obj.bdata)

    # Those settings would obviously need to be edited in a complete version of the exporter, may depends on
    # object type, etc.
    _fbx.elem_data_single_int32(model, b"MultiLayer", 0)
    _fbx.elem_data_single_int32(model, b"MultiTake", 0)
    _fbx.elem_data_single_bool(model, b"Shading", True)
    _fbx.elem_data_single_string(model, b"Culling", b"CullingOff")

    if obj_type == b"Camera":
        # Why, oh why are FBX cameras such a mess???
        # And WHY add camera data HERE??? Not even sure this is needed...
        render = scene_data.scene.render
        width = render.resolution_x * 1.0
        height = render.resolution_y * 1.0
        _fbx.elem_props_template_set(tmpl, props, "p_enum", b"ResolutionMode", 0)  # Don't know what it means
        _fbx.elem_props_template_set(tmpl, props, "p_double", b"AspectW", width)
        _fbx.elem_props_template_set(tmpl, props, "p_double", b"AspectH", height)
        _fbx.elem_props_template_set(tmpl, props, "p_bool", b"ViewFrustum", True)
        _fbx.elem_props_template_set(tmpl, props, "p_enum", b"BackgroundMode", 0)  # Don't know what it means
        _fbx.elem_props_template_set(tmpl, props, "p_bool", b"ForegroundTransparent", True)
        
    # ----------------------- CUSTOM PART BEGINS HERE ----------------------- #

    if obj_type == b"Null" and types.data(ob_obj.bdata):
        se = types.data(ob_obj.bdata)
        if se.file:
            _fbx.elem_props_template_set(tmpl, props, "p_string", b"file", se.file)

    if obj_type == b"Mesh" and ob_obj.bdata.rigid_body:
        rbo = ob_obj.bdata.rigid_body
        shapeType = HAVOK_SHAPE_NAMES[rbo.collision_shape] or rbo.collision_shape
        _fbx.elem_props_template_set(tmpl, props, "p_string", b"hkTypeRigidBody", "hkRigidBody")
        _fbx.elem_props_template_set(tmpl, props, "p_double", b"mass", rbo.mass)
        _fbx.elem_props_template_set(tmpl, props, "p_double", b"friction", rbo.friction)
        _fbx.elem_props_template_set(tmpl, props, "p_double", b"restitution", rbo.restitution)
        _fbx.elem_props_template_set(tmpl, props, "p_string", b"hkTypeShape", "hkShape")
        _fbx.elem_props_template_set(tmpl, props, "p_string", b"shapeType", shapeType)

    # ------------------------ CUSTOM PART ENDS HERE ------------------------ #

    _fbx.elem_props_template_finalize(tmpl, props)

_fbx.fbx_data_object_elements = fbx_data_object_elements

# export these two functions as our own so that clients of this module don't have to depend on 
# the cloned fbx_experimental.export_fbx_bin module
save_single = _fbx.save_single
save = _fbx.save

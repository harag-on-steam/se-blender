from collections import OrderedDict
import os
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory
import bpy
from bpy.utils import register_class, unregister_class
from .export import ExportSettings, MissbehavingToolError
from .mirroring import setupMirrors
from .merge_xml import CubeBlocksMerger, MergeResult
from .mount_points import create_mount_point_skeleton
from .types import getExportNodeTreeFromContext, getExportNodeTree, data, sceneData
from .nodes import BlockDefinitionNode, Exporter, BlockExportTree, getBlockDef, LayerObjectsNode, SeparateLayerObjectsNode
from .utils import currentSceneHolder, layers, layer_bits, layer_bit, PinnedScene
from .default_nodes import createDefaultTree

# mapping (scene.block_size) -> (block_size_name, apply_scale_down)
SIZES = {
    'LARGE' : [('Large', False)],
    'SMALL' : [('Small', False)],
    'SCALE_DOWN' : [('Large', False), ('Small', True)]
}

class BlockExport:
    def __init__(self, settings: ExportSettings):
        self.settings = settings

    def mergeBlockDefs(self, cubeBlocks: CubeBlocksMerger):
        settings = self.settings

        with PinnedScene(settings.scene):
            blockdefNode = None
            for n in settings.exportNodes.nodes:
                if isinstance(n, BlockDefinitionNode):
                    blockdefNode = n
                    break

            if blockdefNode is None:
                settings.error("No block-definition node in export node-tree '%s'" % (settings.exportNodes.name))
                return False

            failed = False
            for settings.CubeSize, settings.scaleDown in SIZES[settings.sceneData.block_size]:
                settings.cache.clear()

                try:
                    xml = blockdefNode.generateBlockDefXml(settings)
                except ValueError as e:
                    settings.error(str(e), node=blockdefNode)
                    failed = True
                    continue

                result = cubeBlocks.merge(xml)
                if MergeResult.NOT_FOUND in result:
                    failed = True
                    settings.warn("CubeBlocks.sbc contained no definition for SubtypeId [%s]" % (settings.SubtypeId))
                elif MergeResult.MERGED in result:
                    settings.info("Updated SubtypeId [%s]" % (settings.SubtypeId))

        return not failed

    def export(self):
        settings = self.settings

        skips = OrderedDict()
        failures = OrderedDict()

        with PinnedScene(settings.scene):
            for settings.CubeSize, settings.scaleDown in SIZES[settings.sceneData.block_size]:
                settings.cache.clear()

                for exporter in settings.exportNodes.nodes:
                    if not isinstance(exporter, Exporter):
                        continue

                    name = exporter.label if exporter.label else exporter.name
                    result = exporter.export(settings)
                    if 'SKIPPED' == result:
                        skips[name] = exporter
                    elif 'FAILED' == result:
                        failures[name] = exporter

        if skips:
            settings.warn("Some export-nodes were skipped: %s" % list(skips.keys()))
        if failures:
            settings.error("Some export-nodes failed: %s" % list(failures.keys()))

class ExportSceneAsBlock(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_block"
    bl_label = "Export Space Engineers Block"
    bl_description = "Exports the current scene as a block. Hold ALT to export all scenes."

    directory = bpy.props.StringProperty(subtype='DIR_PATH')

    all_scenes = bpy.props.BoolProperty(
        name="All Scenes",
        description="Export all scenes that are marked as blocks.")
    skip_mwmbuilder = bpy.props.BoolProperty(
        name="Skip mwmbuilder",
        description="Export intermediary files but do not run them through mwmbuilder")
    use_tspace = bpy.props.BoolProperty(
        name="Tangent Space",
        description="Add binormal and tangent vectors, together with normal they form the tangent space "
                    "(will only work correctly with tris/quads only meshes!)",
        default=False)

    settings_name = bpy.props.StringProperty(
        name="Used Settings",
        description="The name of the node-tree that defines the export",
        default="")

    @classmethod
    def poll(self, context):
        if not context.scene:
            return False

        d = data(context.scene)
        if d is None or not d.is_block:
            return False

        tree = getExportNodeTreeFromContext(context)
        return not tree is None

    def invoke(self, context, event):
        if context.space_data.type == 'INFO':
            # exporting via the export-menu asks for an export-directory
            if not self.directory:
                self.directory = os.path.dirname(context.blend_data.filepath)

            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        else:
            # anywhere else the export-path uses the scene's properties
            self.all_scenes = event.alt
            return self.execute(context)

    def draw(self, context):
        lay = self.layout

        col = lay.column()
        col.prop(self, "all_scenes")
        col.prop(self, "skip_mwmbuilder")
        col.prop(self, "use_tspace")

    def execute(self, context):
        org_mode = None

        try:
            # only object-mode has no pending changes to the meshes
            if context.active_object and context.active_object.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
                org_mode = context.active_object.mode
                bpy.ops.object.mode_set(mode='OBJECT')

            if self.all_scenes:
                scenes = [scene for scene in bpy.data.scenes if data(scene).is_block]
            else:
                scenes = [context.scene]

            with TemporaryDirectory() as tmpDir:
                wm = context.window_manager
                wm.progress_begin(0, len(scenes))
                try:
                    for i, scene in enumerate(scenes):
                        # exporting via the export-menu explicitly asks for an export-directory
                        outputDir = self.directory if context.space_data.type == 'INFO' else None
                        # exporting all nodes will use their respective export-settings
                        exportSettings = getExportNodeTree(self.settings_name) if not self.all_scenes else None
                        settings = ExportSettings(scene, outputDir, exportSettings, tmpDir)

                        settings.operator = self
                        settings.isRunMwmbuilder = not self.skip_mwmbuilder
                        settings.isUseTangentSpace = self.use_tspace

                        BlockExport(settings).export()

                        wm.progress_update(i)
                finally:
                    wm.progress_end()

        except FileNotFoundError as e: # raised when the addon preferences are missing some tool paths
            self.report({'ERROR'}, "Configuration error: %s" % e)

        except CalledProcessError as e:
            self.report({'ERROR'}, "An external tool failed, check generated logs: %s" % e)

        except MissbehavingToolError as e:
            self.report({'ERROR'}, str(e))

        finally:
            if context.active_object and org_mode and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode=org_mode)

        return {'FINISHED'}

class UpdateDefinitionsFromBlockScene(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_update_definitions"
    bl_label = "Update Block Definitions"
    bl_description = "Update the block-definitions in CubeBlocks.sbc."

    filepath = bpy.props.StringProperty(subtype='FILE_PATH')

    all_scenes = bpy.props.BoolProperty(
        name="All Scenes",
        description="Update with data from all scenes that are marked as blocks.")
    create_backup = bpy.props.BoolProperty(
        name="Backup Target File",
        description="Creates a backup of the target file before updating.")
    allow_renames = bpy.props.BoolProperty(
        name="Update SubtypeIds",
        description="Renames the SubtypeId if a definition matches by BlockPairName and CubeSize. "
                    "Be aware that this is not backwards-compatible for players!")

    settings_name = bpy.props.StringProperty(
        name="Used Settings",
        description="The name of the node-tree that defines the export",
        default="MwmExport")

    @classmethod
    def poll(self, context):
        if not context.scene:
            return False

        d = data(context.scene)
        if d is None or not d.is_block:
            return False

        tree = getExportNodeTreeFromContext(context)
        if tree is None or not any(n for n in tree.nodes if n.bl_idname == "SEBlockDefNode"):
            return False

        return True

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = os.path.dirname(context.blend_data.filepath)

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        lay = self.layout

        col = lay.column()
        col.prop(self, "all_scenes")
        col.prop(self, "create_backup")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        merger = CubeBlocksMerger(cubeBlocksPath=path, backup=self.create_backup)

        if self.all_scenes:
            scenes = [scene for scene in bpy.data.scenes if data(scene).is_block]
        else:
            scenes = [context.scene]

        wm = context.window_manager
        wm.progress_begin(0, len(scenes))
        try:
            for i, scene in enumerate(scenes):
                settings = ExportSettings(scene)
                settings.operator = self

                BlockExport(settings).mergeBlockDefs(merger)

                wm.progress_update(i)

            merger.write()
        finally:
            wm.progress_end()

        return {'FINISHED'}

class AddDefaultExportNodes(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_export_nodes"
    bl_label = "Add Default Export-Settings"
    bl_description = "Creates a new exporter node-tree with default settings."

    @classmethod
    def poll(self, context):
        if context.space_data.type == 'NODE_EDITOR' and not isinstance(context.space_data.node_tree, BlockExportTree):
            return False

        return True

    def execute(self, context):
        if context.space_data.type == 'NODE_EDITOR':
            tree = context.space_data.node_tree
        else:
            tree = bpy.data.node_groups.new('MwmExport', BlockExportTree.bl_idname)

        createDefaultTree(tree)
        tree.use_fake_user = True

        return {'FINISHED'}

class NameLayersFromExportNodes(bpy.types.Operator):
    bl_idname = "object.space_engineers_layer_names"
    bl_label = "Name Layers after Export Nodes"
    bl_description = "If the Layer Manager addon is enabled, " \
                     "this names the layers of the current scene after the export-nodes that read from them."

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "namedlayers", None) is not None

    def execute(self, context):
        nodeTree = context.scene.space_engineers.getExportNodeTree()
        namedLayers = context.scene.namedlayers.layers

        def layer_indices(layers):
            return (i for i, l in enumerate(layers) if l)

        def node_label(node):
            return node.label if node.label else node.name

        for n in nodeTree.nodes:
            if isinstance(n, LayerObjectsNode):
                for li in layer_indices(n.layer_mask):
                    namedLayers[li].name = node_label(n)
            elif isinstance(n, SeparateLayerObjectsNode):
                for i, li in enumerate(layer_indices(n.layer_mask)):
                    namedLayers[li].name = "%s %d" % (node_label(n), i+1)

        return {'FINISHED'}

class AddMirroringEmpties(bpy.types.Operator):
    bl_idname = "object.space_engineers_mirrors"
    bl_label = "Block Mirroring"
    bl_description = "Creates or rebuilds empties to model the block-mirroring. " \
                     "Rotate those empties to configure the mirroring for the corresponding axes."

    @classmethod
    def poll(self, context):
        return True

    def execute(self, context):
        blockData = sceneData(context.scene)
        isSmall = blockData.block_size == 'SMALL'

        try:
            blockDef = getBlockDef(blockData.getExportNodeTree())
            layer = blockDef.getMirroringLayer()

            mirrorData = blockData.getMirroringBlock()
            if not mirrorData is None:
                with PinnedScene(mirrorData.scene):
                    mirrorBlockDef = getBlockDef(mirrorData.getExportNodeTree())
                    mainObjects = mirrorBlockDef.getMainObjects()
            else:
                mainObjects = blockDef.getMainObjects()

        except ValueError as e:
            self.report({'ERROR'}, "Invalid export settings: " + str(e))
            return {'FINISHED'}

        setupMirrors(context.scene, mainObjects, blockData.block_dimensions, isSmall, layer)

        if layer >= 0:
            context.scene.layers = layers(layer_bits(context.scene.layers) | layer_bit(layer))
        else:
            self.report({'WARNING'}, "No mirroring layer defined. Objects were created on active layer.")

        return {'FINISHED'}

class AddMountPointSkeleton(bpy.types.Operator):
    bl_idname = 'object.spceng_mountpoint_add'
    bl_label = 'Mount-Points'
    bl_options = {'REGISTER'}
    bl_description = \
        "Creates an object with six rectangular mount-point faces, one for each side of the block. " \
        "Duplicate these faces in edit-mode or use modifiers to create additional mount-points."

    def execute(self, context):
        s = context.scene

        ob = create_mount_point_skeleton()
        ob.location = (0, 0, 0)
        ob.lock_location = (True, True, True)
        ob.lock_rotation = (True, True, True)
        ob.lock_scale = (True, True, True)

        s.objects.link(ob)

        try:
            layer = getBlockDef(sceneData(s).getExportNodeTree()).getMountPointLayer()
        except ValueError:
            layer = -1

        if layer >= 0:
            ob.layers = layers(layer_bit(layer))
            s.layers = layers(layer_bits(s.layers) | layer_bit(layer))
        else:
            self.report({'WARNING'}, "No mount-point layer defined. Objects were created on active layer.")

        return {'FINISHED'}

class SetupGrid(bpy.types.Operator):
    bl_idname = 'view3d.spceng_setup_grid'
    bl_label = 'Set up Grid'
    bl_options = {'REGISTER'}
    bl_description = \
        "Sets the view-grid to a scaling of 1.25 with 5 subdivision. " \
        "This way you get 10 steps per one large block-cube."

    def execute(self, context):
        space = context.space_data

        space.grid_scale = 1.25
        space.grid_subdivisions = 5

        if (space.grid_lines < 21):
            space.grid_lines = 21

        return {'FINISHED'}

registered = [
    AddDefaultExportNodes,
    AddMirroringEmpties,
    ExportSceneAsBlock,
    UpdateDefinitionsFromBlockScene,
    AddMountPointSkeleton,
    SetupGrid,
    NameLayersFromExportNodes,
]

def register():
    for c in registered:
        register_class(c)

def unregister():
    for c in reversed(registered):
        unregister_class(c)

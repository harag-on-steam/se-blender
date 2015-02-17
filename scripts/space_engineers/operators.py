import os
from subprocess import CalledProcessError
import bpy
from .export import ExportSettings
from .merge_xml import CubeBlocksMerger, MergeResult
from .types import getExportNodeTreeFromContext, getExportNodeTree, data
from .nodes import BlockDefinitionNode, Exporter, BlockExportTree
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

        skips = False
        failures = False

        for settings.CubeSize, settings.scaleDown in SIZES[settings.sceneData.block_size]:
            settings.cache.clear()

            for exporter in settings.exportNodes.nodes:
                if not isinstance(exporter, Exporter):
                    continue

                result = exporter.export(settings)
                if 'SKIPPED' == result:
                    skips = True
                elif 'FAILED' == result:
                    failures = True

        if skips:
            settings.warn("Some export-nodes were skipped.")
        if failures:
            settings.error("Some export-nodes failed.")

class ExportSceneAsBlock(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_block"
    bl_label = "Export Space Engineers Block"
    bl_description = "Exports the current scene as a block."

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
        if not self.directory:
            self.directory = os.path.dirname(context.blend_data.filepath)

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        lay = self.layout

        col = lay.column()
        # FIXME node graph does not use settings.scene, yet
        # col.prop(self, "all_scenes")
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

            wm = context.window_manager
            wm.progress_begin(0, len(scenes))
            try:
                for i, scene in enumerate(scenes):
                    settings = ExportSettings(scene, self.directory, getExportNodeTree(self.settings_name))
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
        # FIXME node graph does not use settings.scene, yet
        # col.prop(self, "all_scenes")
        col.prop(self, "create_backup")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        dir = os.path.dirname(path)
        merger = CubeBlocksMerger(cubeBlocksPath=path, backup=self.create_backup)

        if self.all_scenes:
            scenes = [scene for scene in bpy.data.scenes if data(scene).is_block]
        else:
            scenes = [context.scene]

        wm = context.window_manager
        wm.progress_begin(0, len(scenes))
        try:
            for i, scene in enumerate(scenes):
                settings = ExportSettings(scene, dir)
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


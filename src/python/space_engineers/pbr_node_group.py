from enum import Enum
import bpy
from .texture_files import TextureType


class MathOperation(Enum):
    ADD = 1
    SUBTRACT = 2
    MULTIPLY = 3
    DIVIDE = 4
    SINE = 5
    COSINE = 6
    TANGENT = 7
    ARCSINE = 8
    ARCCOSINE = 9
    ARCTANGENT = 10
    POWER = 11
    LOGARITHM = 12
    MINIMUM = 13
    MAXIMUM = 14
    ROUND = 15
    LESS_THAN = 16
    GREATER_THAN = 17
    MODULO = 18
    ABSOLUTE = 19

class NormalSpace(Enum):
    TANGENT = 1
    OBJECT = 2
    WORLD = 3
    BLENDER_OBJECT = 4
    BLENDER_WORLD = 5

class GlossyDistribution(Enum):
    SHARP = 1
    BECKMANN = 2
    GGX = 3
    ASHIKHMIN_SHIRLEY = 4

class ImageColorspace(Enum):
    COLOR = 1
    NONE = 2

def blId(nodeType):
    return nodeType.bl_rna.identifier

class ShaderNodesBuilder:
    def __init__(self, tree: bpy.types.NodeTree):
        self.tree = tree

    def _newNode(self, nodeType, label=None, location=(0,0)):
        node = self.tree.nodes.new(blId(nodeType))
        if not label is None:
            node.label = label
        node.location = location
        return node

    def _connectSockets(self, pairs):
        for source, target in pairs:
            if not source is None:
                if isinstance(source, bpy.types.NodeSocket):
                    self.tree.links.new(source, target)
                else:
                    target.default_value = source

    def newMath(self, label=None, location=(0,0), op=MathOperation.ADD, clamp=False, op1=None, op2=None):
        n = self._newNode(bpy.types.ShaderNodeMath, label, location)
        if not op is None: n.operation = op.name
        if not clamp is None: n.use_clamp = clamp
        self._connectSockets(((op1, n.inputs[0]), (op2, n.inputs[1])))
        return n.outputs[0]

    def newGlossy(self, label=None, location=(0,0), distribution=GlossyDistribution.GGX, color=None, roughness=None, normal=None):
        n = self._newNode(bpy.types.ShaderNodeBsdfGlossy, label, location)
        if not distribution is None: n.distribution = distribution.name
        self._connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newDiffuse(self, label=None, location=(0,0), color=None, roughness=None, normal=None):
        n = self._newNode(bpy.types.ShaderNodeBsdfDiffuse, label, location)
        self._connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newFresnel(self, label=None, location=(0,0), ior=None, normal=None):
        n = self._newNode(bpy.types.ShaderNodeFresnel, label, location)
        self._connectSockets(((ior, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newMix(self, label=None, location=(0,0), fac=None, shader1=None, shader2=None):
        n = self._newNode(bpy.types.ShaderNodeMixShader, label, location)
        self._connectSockets(((fac, n.inputs[0]), (shader1, n.inputs[1]), (shader2, n.inputs[2])))
        return n.outputs[0]

    def newAdd(self, label=None, location=(0,0), shader1=None, shader2=None):
        n = self._newNode(bpy.types.ShaderNodeAddShader, label, location)
        self._connectSockets(((shader1, n.inputs[0]), (shader2, n.inputs[1])))
        return n.outputs[0]

    def newNormalMap(self, label=None, location=(0,0), space=NormalSpace.TANGENT, uvMapName=None, strength=None, normal=None):
        n = self._newNode(bpy.types.ShaderNodeNormalMap, label, location)
        if not space is None: n.space = space.name
        if not uvMapName is None: n.uv_map = uvMapName
        self._connectSockets(((strength, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newSeparateRgb(self, label=None, location=(0,0), color=None):
        n = self._newNode(bpy.types.ShaderNodeSeparateRGB, label, location)
        self._connectSockets([(color, n.inputs[0])])
        return [n.outputs[0], n.outputs[1], n.outputs[2]] # rgb

    def newImageTexture(self, label=None, location=(0,0), space=ImageColorspace.COLOR, image=None):
        n = self._newNode(bpy.types.ShaderNodeTexImage, label, location)
        if not label is None: n.name = label
        if not space is None: n.color_space = space.name
        if not image is None: n.image = image
        return [n.outputs[0], n.outputs[1]] # color + alpha

    def newRgbValue(self, label=None, location=(0,0), default=None):
        n = self._newNode(bpy.types.ShaderNodeRGB, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newFloatValue(self, label=None, location=(0,0), default=None):
        n = self._newNode(bpy.types.ShaderNodeValue, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newFloatInput(self, label, default=None, min=None, max=None):
        input = self.tree.inputs.new(blId(bpy.types.NodeSocketFloatUnsigned), label)
        if not default is None: input.default_value = default
        if not min is None: input.min_value = min
        if not max is None: input.max_value = max
        return input

    def newColorInput(self, label, default=(0.8, 0.8, 0.8, 1.0)): # default Blender near-white
        input = self.tree.inputs.new(blId(bpy.types.NodeSocketColor), label)
        if not default is None: input.default_value = default
        return input

DX11_NAME = 'SpaceEngineers_DX11_Shader'
DX9_NAME = 'SpaceEngineers_DX9_Shader'

def createDx11ShaderGroup():
    pbr = bpy.data.node_groups.new(DX11_NAME, blId(bpy.types.ShaderNodeTree))

    builder = ShaderNodesBuilder(pbr)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-groupt

    # ColorMetalTexture
    builder.newColorInput("Base Color")
    builder.newFloatInput("Metalness", 0.0, 0.0, 1.0)
    # NormalGlossTexture
    builder.newColorInput("Normal Map", (0.5, 0.5, 1.0, 1.0))
    builder.newFloatInput("Glossiness", 0.0, 0.0, 1.0)
    # AddMapsTexture
    builder.newColorInput("AO/Emissivity", (1, 0, 0, 1)) # R: no AO, G: no emissivity, B: unused
    builder.newFloatInput("Coloring Mask", 0.0, 0.0, 1.0)

    pbr.outputs.new(blId(bpy.types.NodeSocketShader), "Surface")

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets within the node-tree of the node-group

    inputs = pbr.nodes.new(blId(bpy.types.NodeGroupInput))
    inputs.location = (-400, 0)
    baseColor, metalness, normalMap, glossiness, addMaps, coloringMask = inputs.outputs[0:6]
    # ao, emissivity, _ = builder.newSeparateRGB(addMaps)

    outputs = pbr.nodes.new(blId(bpy.types.NodeGroupOutput))
    outputs.location = (800, 0)
    shader = outputs.inputs[0]

    # ------------------------------------------------------------------------------------------------------------#

    # SE textures provide glossiness, Blender expects the inverse: roughness
    invertedGloss = builder.newMath("Invert", (-200, 0), MathOperation.SUBTRACT, False, 1.0, glossiness)
    roughness = builder.newMath("^2", (0, 0), MathOperation.POWER, False, invertedGloss, 2.0)

    normal = builder.newNormalMap(None, (-200, -200), NormalSpace.TANGENT, None, None, normalMap)

    fresnelColor = builder.newFresnel("Fresnel Diffuse", (200, -600), 1.5, normal)
    diffuseColor = builder.newDiffuse("Diffuse Color", (200, -400), baseColor, roughness, normal)
    glossyColor = builder.newGlossy("Diffuse Gloss", (200, -200), GlossyDistribution.GGX, (1,1,1,1), roughness, normal)
    mixColor = builder.newMix(None, (400, -400), fresnelColor, diffuseColor, glossyColor)

    glossyMetal = builder.newGlossy("Metal Gloss", (200, 0), GlossyDistribution.GGX, baseColor, roughness, normal)
    fresnelMetal = builder.newFresnel("Fresnel Metal ", (200, 200), 25.0, normal)
    factorMetal = builder.newMath(None, (400, 200), MathOperation.MULTIPLY, True, fresnelMetal, metalness)
    mixMetal = builder.newMix(None, (600, 0), factorMetal, mixColor, glossyMetal)

    pbr.links.new(mixMetal, shader)
    pbr.use_fake_user = True

def createDx9ShaderGroup():
    pbr = bpy.data.node_groups.new(DX9_NAME, blId(bpy.types.ShaderNodeTree))
    builder = ShaderNodesBuilder(pbr)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-group

    # DiffuseTexture
    builder.newColorInput("Diffuse")
    builder.newFloatInput("Emissive", 1.0, 0.0, 1.0) # SE considers 1.0 as "not emissive"
    # NormalTexture
    builder.newColorInput("Normal Map", (0.5, 0.5, 1.0, 1.0))
    builder.newFloatInput("Specularity", 0.0, 0.0, 1.0)
    # static values
    builder.newColorInput("Uniform Color", (1, 1, 1, 1))
    builder.newFloatInput("Specular Intensity", 0.0, 0.0, 1000.0)
    builder.newFloatInput("Specular Power", 0.0, 0.0, 1000.0)

    pbr.outputs.new(blId(bpy.types.NodeSocketShader), "Surface")

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets within the node-tree of the node-group

    inputs = pbr.nodes.new(blId(bpy.types.NodeGroupInput))
    inputs.location = (-400, 0)
    diffuse, emissivity, normalMap, specularity, uniColor, specInt, specPow = inputs.outputs[0:7]

    outputs = pbr.nodes.new(blId(bpy.types.NodeGroupOutput))
    outputs.location = ( 200, 0)
    shader = outputs.inputs[0]

    # ------------------------------------------------------------------------------------------------------------#

    normal = builder.newNormalMap(None, (-200, -200), NormalSpace.TANGENT, None, None, normalMap)

    diffuseColor = builder.newDiffuse("Diffuse Color", (-200, 0), diffuse, 0.0, normal)

    pbr.links.new(diffuseColor, shader)
    pbr.use_fake_user = True


def getDx9Shader():
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX9_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        createDx9ShaderGroup()
        return getDx9Shader()

def getDx11Shader():
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX11_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        createDx11ShaderGroup()
        return getDx11Shader()


def createMaterialNodeTree(tree: bpy.types.ShaderNodeTree):
    builder = ShaderNodesBuilder(tree)
    tree.nodes.clear()

    def label(type):
        return type.name + "Texture"

    cmC , cmA  = builder.newImageTexture(label(TextureType.ColorMetal),  (-600, 600), ImageColorspace.COLOR)
    ngC , ngA  = builder.newImageTexture(label(TextureType.NormalGloss), (-400, 500), ImageColorspace.NONE)
    addC, addA = builder.newImageTexture(label(TextureType.AddMaps),     (-200, 400), ImageColorspace.NONE)
    alphaC, _  = builder.newImageTexture(label(TextureType.Alphamask),   (   0, 300), ImageColorspace.NONE)

    dx11 = builder._newNode(bpy.types.ShaderNodeGroup, None, (100, 600))
    dx11.name = "DX11Shader"
    dx11.node_tree = getDx11Shader()
    builder._connectSockets(pair for pair in zip([cmC, cmA, ngC, ngA, addC, addA], dx11.inputs[0:6]))
    dx11.width = 207

    frameDx11 = builder._newNode(bpy.types.NodeFrame, 'DirectX 11 Textures')
    frameDx11.name = "DX11Frame"
    frameDx11.color = (0.30, 0.50, 0.66)
    frameDx11.use_custom_color = True
    frameDx11.shrink = True
    frameDx11.label_size = 25
    for n in (cmC.node, ngC.node, addC.node, alphaC.node, dx11):
        n.parent = frameDx11

    deC , deA  = builder.newImageTexture(label(TextureType.Diffuse), (-600, -100), ImageColorspace.COLOR)
    nsC , nsA  = builder.newImageTexture(label(TextureType.Normal),  (-400, -200), ImageColorspace.NONE)

    uniColor   = builder.newRgbValue  ("Diffuse Color",      (-200, -300), (1,1,1,1))
    specInt    = builder.newFloatValue("Specular Intensity", (-200, -500), 0)
    specPow    = builder.newFloatValue("Specular Power",     (-200, -600), 0)
    uniColor.node.name = "DiffuseColor"
    specInt.node.name  = "SpecularIntensity"
    specPow.node.name  = "SpecularPower"

    dx9 = builder._newNode(bpy.types.ShaderNodeGroup, None, (100, -100))
    dx9.name = "DX9Shader"
    dx9.node_tree = getDx9Shader()
    builder._connectSockets(pair for pair in zip([deC, deA, nsC, nsA, uniColor, specInt, specPow], dx9.inputs[0:7]))
    dx9.width = 207

    frameDx9 = builder._newNode(bpy.types.NodeFrame, 'DirectX 9 Textures')
    frameDx9.name = "DX9Frame"
    frameDx9.color = (0.67, 0.67, 0.39)
    frameDx9.use_custom_color = True
    frameDx9.shrink = True
    frameDx9.label_size = 25
    for n in (deC.node, nsC.node, uniColor.node, specInt.node, specPow.node, dx9):
        n.parent = frameDx9

    out = builder._newNode(bpy.types.ShaderNodeOutputMaterial, None, (600, 200))
    builder._connectSockets([(dx11.outputs[0], out.inputs[0])])

    #material.use_nodes = True

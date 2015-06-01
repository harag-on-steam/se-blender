from enum import Enum
import re
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

class CreateMode(Enum):
    ADD = 1
    REPLACE = 2
    REUSE = 3

def blId(nodeType):
    return nodeType.bl_rna.identifier

_RE_BLENDER_NAME = re.compile(r"^(.+?)(?:\.(\d+)+)?$")
_RE_WHITESPACE = re.compile(r"(?:\s|[-+/.])+")

def firstMatching(iterable, type, name=None):
    if not name is None:
        for item in iterable:
            if not isinstance(item, type):
                continue
            match = _RE_BLENDER_NAME.match(item.name)
            if match and match.group(1) == name:
                return item
    else:
        for item in iterable:
            if isinstance(item, type):
                return item
    return None

class SocketSpec:
    def __init__(self, type, name, default=None, min=None, max=None):
        self.type = type
        self.name = name
        self.default = default
        self.min = min
        self.max = max

class ShaderNodesBuilder:
    def __init__(self, tree: bpy.types.NodeTree, defaultCreate=CreateMode.ADD):
        self.tree = tree
        self.defaultCreate = defaultCreate

    def newNode(self, nodeType, name=None, label=None, location=None, width=None, create=None):
        if name is None and label:
            name = _RE_WHITESPACE.sub("", label)
        if create is None: create = self.defaultCreate

        node = None
        if create in {CreateMode.REPLACE, CreateMode.REUSE}:
            node = firstMatching(self.tree.nodes, nodeType, name)
            if not node is None and create == CreateMode.REPLACE:
                self.tree.nodes.remove(node)
                node = None
        if node is None:
            node = self.tree.nodes.new(blId(nodeType))

        if not label is None: node.label = label
        if not name is None: node.name = name
        if not location is None: node.location = location
        if not width is None: node.width = width

        return node

    def connectSockets(self, pairs):
        for source, target in pairs:
            if not source is None:
                if isinstance(source, bpy.types.NodeSocket):
                    self.tree.links.new(source, target)
                else:
                    target.default_value = source

    def newMath(self, name=None, label=None, location=None, op=None, clamp=None, op1=None, op2=None):
        n = self.newNode(bpy.types.ShaderNodeMath, name, label, location)
        if not op is None: n.operation = op.name
        if not clamp is None: n.use_clamp = clamp
        self.connectSockets(((op1, n.inputs[0]), (op2, n.inputs[1])))
        return n.outputs[0]

    def newGlossy(self, name=None, label=None, location=None, distribution=None, color=None, roughness=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeBsdfGlossy, name, label, location)
        if not distribution is None: n.distribution = distribution.name
        self.connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newDiffuse(self, name=None, label=None, location=None, color=None, roughness=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeBsdfDiffuse, name, label, location)
        self.connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newFresnel(self, name=None, label=None, location=None, ior=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeFresnel, name, label, location)
        self.connectSockets(((ior, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newMix(self, name=None, label=None, location=None, factor=None, shader1=None, shader2=None):
        n = self.newNode(bpy.types.ShaderNodeMixShader, name, label, location)
        self.connectSockets(((factor, n.inputs[0]), (shader1, n.inputs[1]), (shader2, n.inputs[2])))
        return n.outputs[0]

    def newAdd(self, name=None, label=None, location=None, shader1=None, shader2=None):
        n = self.newNode(bpy.types.ShaderNodeAddShader, name, label, location)
        self.connectSockets(((shader1, n.inputs[0]), (shader2, n.inputs[1])))
        return n.outputs[0]

    def newNormalMap(self, name=None, label=None, location=None, space=None, uvMapName=None, strength=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeNormalMap, name, label, location)
        if not space is None: n.space = space.name
        if not uvMapName is None: n.uv_map = uvMapName
        self.connectSockets(((strength, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newSeparateRgb(self, name=None, label=None, location=None, color=None):
        n = self.newNode(bpy.types.ShaderNodeSeparateRGB, name, label, location)
        self.connectSockets((color, n.inputs[0]))
        return (n.outputs[0], n.outputs[1], n.outputs[2]) # rgb

    def newImageTexture(self, name=None, label=None, location=None, space=None, image=None):
        n = self.newNode(bpy.types.ShaderNodeTexImage, name, label, location)
        if not label is None: n.name = label
        if not space is None: n.color_space = space.name
        if not image is None: n.image = image
        return (n.outputs[0], n.outputs[1]) # color + alpha

    def newRgbValue(self, name=None, label=None, location=None, default=None):
        n = self.newNode(bpy.types.ShaderNodeRGB, name, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newFloatValue(self, name=None, label=None, location=None, default=None):
        n = self.newNode(bpy.types.ShaderNodeValue, name, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newTreeSockets(self, socketSpecs, output=False, location=None):
        socketCollection = self.tree.outputs if output else self.tree.inputs

        # keep sockets until they do not match in type
        unmatched = min(len(socketSpecs), len(socketCollection))
        for i, (socket, spec) in enumerate(zip(socketCollection, socketSpecs)):
            if not isinstance(socket, spec.type):
                unmatched = i
                break

        # prune, beginning with the first non-matching socket and add missing sockets after that according to spec
        while (unmatched < len(socketCollection)):
            socketCollection.remove(socketCollection[len(socketCollection)-1])
        for spec in socketSpecs[unmatched:]:
            socketCollection.new(blId(spec.type), spec.name)

        # configure all sockets according to spec
        for socket, spec in zip(socketCollection, socketSpecs):
            socket.name = spec.name
            if not spec.default is None: socket.default_value = spec.default
            if not spec.min is None: socket.min_value = spec.min
            if not spec.max is None: socket.max_value = spec.max

        if output:
            n = self.newNode(bpy.types.NodeGroupOutput, location=location, create=CreateMode.REUSE)
            return [s for s in n.inputs[0:len(socketSpecs)]]
        else:
            n = self.newNode(bpy.types.NodeGroupInput, location=location, create=CreateMode.REUSE)
            return [s for s in n.outputs[0:len(socketSpecs)]]

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
    pbr = firstMatching(bpy.data.node_groups, bpy.types.ShaderNodeTree, DX11_NAME)
    if not pbr:
        pbr = bpy.data.node_groups.new(DX11_NAME, blId(bpy.types.ShaderNodeTree))
    builder = ShaderNodesBuilder(pbr, defaultCreate=CreateMode.REUSE)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-group

    socketSpecs = [
        # ColorMetalTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Base Color"),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Metalness", 0.0, 0.0, 1.0),
        # NormalGlossTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Normal Map", (0.5, 0.5, 1.0, 1.0)), # straight up
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Glossiness", 0.0, 0.0, 1.0),
        # AddMapsTexture
        SocketSpec(bpy.types.NodeSocketColor,         "AO/Emissivity", (1, 0, 0, 1)), # R: no AO, G: no emis, B: unused
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Coloring Mask", 0.0, 0.0, 1.0),
    ]
    baseColor, metalness, normalMap, glossiness, addMaps, coloringMask = \
        builder.newTreeSockets(socketSpecs, False, (-400, 0))

    socketSpecs = [
        SocketSpec(bpy.types.NodeSocketShader, "Surface")
    ]
    shader = builder.newTreeSockets(socketSpecs, True, (800, 0))[0]

    # ------------------------------------------------------------------------------------------------------------#

    # ao, emissivity, _ = builder.newSeparateRGB(addMaps)
    # SE textures provide glossiness, Blender expects the inverse: roughness
    invertedGloss = builder.newMath(None, "Invert", (-200, 0), MathOperation.SUBTRACT, False, 1.0, glossiness)
    roughness = builder.newMath(None, "Pow2", (0, 0), MathOperation.POWER, False, invertedGloss, 2.0)

    normal = builder.newNormalMap("NormalMap", None, (-200, -200), NormalSpace.TANGENT, None, None, normalMap)

    fresnelColor = builder.newFresnel(None, "Fresnel Diffuse", (200, -600), 1.5, normal)
    diffuseColor = builder.newDiffuse(None, "Diffuse Color", (200, -400), baseColor, roughness, normal)
    glossyColor = builder.newGlossy(None, "Diffuse Gloss", (200, -200), GlossyDistribution.GGX, (1,1,1,1), roughness, normal)
    mixColor = builder.newMix("MixColor", None, (400, -400), fresnelColor, diffuseColor, glossyColor)

    glossyMetal = builder.newGlossy(None, "Metal Gloss", (200, 0), GlossyDistribution.GGX, baseColor, roughness, normal)
    fresnelMetal = builder.newFresnel(None, "Fresnel Metal ", (200, 200), 25.0, normal)
    factorMetal = builder.newMath("MetalFactor", None, (400, 200), MathOperation.MULTIPLY, True, fresnelMetal, metalness)
    mixMetal = builder.newMix("MixMetal", None, (600, 0), factorMetal, mixColor, glossyMetal)

    builder.connectSockets([(mixMetal, shader)])
    pbr.use_fake_user = True

def createDx9ShaderGroup():
    pbr = firstMatching(bpy.data.node_groups, bpy.types.ShaderNodeTree, DX9_NAME)
    if not pbr:
        pbr = bpy.data.node_groups.new(DX9_NAME, blId(bpy.types.ShaderNodeTree))
    builder = ShaderNodesBuilder(pbr, defaultCreate=CreateMode.REUSE)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-group

    socketSpecs = [
        # DiffuseTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Diffuse"),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Emissive", 1.0, 0.0, 1.0), # SE considers 1.0 as "not emissive"
        # NormalTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Normal Map", (0.5, 0.5, 1.0, 1.0)), # straight up
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specularity", 0.0, 0.0, 1.0),
        # static values
        SocketSpec(bpy.types.NodeSocketColor,         "Uniform Color",      (1, 1, 1, 1)),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specular Intensity", 0.0, 0.0, 1000.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specular Power",     0.0, 0.0, 1000.0),
    ]
    diffuse, emissivity, normalMap, specularity, uniColor, specInt, specPow = \
        builder.newTreeSockets(socketSpecs, False, (-400, 0))

    socketSpecs = [
        SocketSpec(bpy.types.NodeSocketShader, "Surface")
    ]
    shader = builder.newTreeSockets(socketSpecs, True, (200, 0))[0]

    # ------------------------------------------------------------------------------------------------------------#

    normal = builder.newNormalMap("NormalMap", None, (-200, -200), NormalSpace.TANGENT, None, None, normalMap)

    diffuseColor = builder.newDiffuse(None, "Diffuse Color", (-200, 0), diffuse, 0.0, normal)

    builder.connectSockets([(diffuseColor, shader)])
    pbr.use_fake_user = True


def getDx11Shader():
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX11_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        createDx11ShaderGroup()
        return getDx11Shader()

def getDx9Shader():
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX9_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        createDx9ShaderGroup()
        return getDx9Shader()

def getDx11ShaderGroup(tree: bpy.types.ShaderNodeTree):
    return firstMatching(tree.nodes, bpy.types.ShaderNodeGroup, "DX11Shader")

def getDx9ShaderGroup(tree: bpy.types.ShaderNodeTree):
    return firstMatching(tree.nodes, bpy.types.ShaderNodeGroup, "DX9Shader")

def createMaterialNodeTree(tree: bpy.types.ShaderNodeTree):
    builder = ShaderNodesBuilder(tree, defaultCreate=CreateMode.REUSE)
    # tree.nodes.clear()

    def label(type):
        return type.name + "Texture"

    cmC , cmA  = builder.newImageTexture(None, label(TextureType.ColorMetal),  (-600, 600), ImageColorspace.COLOR)
    ngC , ngA  = builder.newImageTexture(None, label(TextureType.NormalGloss), (-400, 500), ImageColorspace.NONE)
    addC, addA = builder.newImageTexture(None, label(TextureType.AddMaps),     (-200, 400), ImageColorspace.NONE)
    alphaC, _  = builder.newImageTexture(None, label(TextureType.Alphamask),   (   0, 300), ImageColorspace.NONE)

    dx11 = builder.newNode(bpy.types.ShaderNodeGroup, "DX11Shader", None, (250, 600))
    dx11.node_tree = getDx11Shader()
    builder.connectSockets(pair for pair in zip([cmC, cmA, ngC, ngA, addC, addA], dx11.inputs[0:6]))
    dx11.width = 207

    frameDx11 = builder.newNode(bpy.types.NodeFrame, "DX11Frame", 'DirectX 11 Textures')
    frameDx11.color = (0.30, 0.50, 0.66)
    frameDx11.use_custom_color = True
    frameDx11.shrink = True
    frameDx11.label_size = 25
    for n in (cmC.node, ngC.node, addC.node, alphaC.node, dx11):
        n.parent = frameDx11

    deC , deA  = builder.newImageTexture(None, label(TextureType.Diffuse), (-600, -100), ImageColorspace.COLOR)
    nsC , nsA  = builder.newImageTexture(None, label(TextureType.Normal),  (-400, -200), ImageColorspace.NONE)

    uniColor = builder.newRgbValue  (None, "Diffuse Color",      (-200, -300), (1,1,1,1))
    specInt  = builder.newFloatValue(None, "Specular Intensity", (   0, -300), 0.0)
    specPow  = builder.newFloatValue(None, "Specular Power",     (   0, -400), 0.0)

    dx9 = builder.newNode(bpy.types.ShaderNodeGroup, "DX9Shader", None, (250, -50))
    dx9.node_tree = getDx9Shader()
    builder.connectSockets(pair for pair in zip([deC, deA, nsC, nsA, uniColor, specInt, specPow], dx9.inputs[0:7]))
    dx9.width = 207

    frameDx9 = builder.newNode(bpy.types.NodeFrame, "DX9Frame", 'DirectX 9 Textures')
    frameDx9.color = (0.67, 0.67, 0.39)
    frameDx9.use_custom_color = True
    frameDx9.shrink = True
    frameDx9.label_size = 25
    for n in (deC.node, nsC.node, uniColor.node, specInt.node, specPow.node, dx9):
        n.parent = frameDx9

    shaderToggle = builder.newMix(None, "Shader Toggle", (600, 150), 0.0, dx11.outputs[0], dx9.outputs[0])

    out = builder.newNode(bpy.types.ShaderNodeOutputMaterial, None, None, (800, 150))
    builder.connectSockets([(shaderToggle, out.inputs[0])])

    # there might be a single leftover Diffuse shader from Blender's default material layout
    # remove it if it isn't connected to anything
    diffuseShader = firstMatching(tree.nodes, bpy.types.ShaderNodeBsdfDiffuse)
    if diffuseShader \
            and not any(input for input in diffuseShader.inputs if len(input.links) > 0) \
            and not any(output for output in diffuseShader.outputs if len(output.links) > 0):
        tree.nodes.remove(diffuseShader)


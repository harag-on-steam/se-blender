from collections import OrderedDict
import os
import bpy
from xml.etree import ElementTree
from .texture_files import TextureType
from .types import data, SEMaterialInfo, rgb
from pathlib import Path
import re

BAD_PATH = re.compile(r"^(?:[A-Za-z]:|\.\.)?[\\/]")

def conv_se_content_dir(settings):
    conv_se_dir = bpy.path.abspath(bpy.context.user_preferences.addons['space_engineers'].preferences.seDir)
    if not conv_se_dir:
        return settings.baseDir # fallback, if unset
    return os.path.join(os.path.normpath(conv_se_dir), "Content")


def derive_texture_path(settings, filepath):
    def is_in_subpath(relpath):
        return not relpath.startswith('..') and not os.path.isabs(relpath)

    image_path = os.path.normpath(bpy.path.abspath(filepath))

    try:
        relative_to_se = os.path.relpath(image_path, conv_se_content_dir(settings))
        if is_in_subpath(relative_to_se):
            return relative_to_se
    except ValueError:
        pass

    try:
        relative_to_basedir = os.path.relpath(image_path, settings.baseDir)
        print("relbd: "+relative_to_basedir)
        if is_in_subpath(relative_to_basedir):
            return relative_to_basedir
    except ValueError:
        pass

    return image_path

def _floatstr(f):
    return str(round(f, 2))

# fixes the misspelled constant in types.py without the need to update the value in existing .blend files
def _material_technique(technique):
    return "ALPHA_MASKED" if "ALPHAMASK" == technique else technique

def material_xml(settings, mat, file=None, node=None):
    d = data(mat)
    m = SEMaterialInfo(mat)
    technique_param = _material_technique(d.technique)
    matreffound = None

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value
        
    for xmlreffile in settings.matreffiles:
        # if not TextureType.ColorMetal in m.images:
        if not xmlreffile: #don't want it failing if we don't have a file assigned
            xmlreffile = None

        if not xmlreffile is None:
            texTechnique = ("Technique")
            xmlref = ElementTree.parse(xmlreffile).getroot()
            refmaterial = xmlref.find('.//Material[@Name="%s"]' % mat.name)
            if not refmaterial is None:
                matreffound = True
                refmattechnique = refmaterial.find('.//Parameter[@Name="%s"]' % texTechnique)
                if not refmattechnique is None:
                    technique_param = refmattechnique.text
                    break
    
    if technique_param != "AUTO":
        technique_param = _material_technique(d.technique)
    elif matreffound is None:
        technique_param = "MESH"
        
    if m.images.get(TextureType.ColorMetal, None):
        print(mat.name)
        
    if not matreffound == True or _material_technique(d.technique) != "AUTO" or m.images.get(TextureType.ColorMetal, None) or m.images.get(TextureType.NormalGloss, None) or m.images.get(TextureType.AddMaps, None) or m.images.get(TextureType.Alphamask, None):
        e = ElementTree.Element("Material", Name=mat.name)
           
        
        param("Technique", technique_param)
        
        cmpath = None
        for texType in TextureType:
            filepath = m.images.get(texType, None)
            if texType.name == 'ColorMetal' and not filepath is None:
                cmpath = filepath[:-6]
            
            checked = None
            if texType.name == 'ColorMetal':
                checked = 1
            elif texType.name == 'NormalGloss' and d.usetextureng:
                checked = 1
            elif texType.name == 'AddMaps' and d.usetextureadd:
                checked = 1
            elif texType.name == 'Alphamask' and d.usetexturealpha:
                checked = 1 
            
            if filepath is None:
                for xmlreffile in settings.matreffiles: 
                    if not xmlreffile:#don't want it failing if we don't have a file assigned
                            xmlreffile = None
                        
                        
                    if not xmlreffile is None:
                        texTypeS = (texType.name + "Texture")
                        xmlref = ElementTree.parse(xmlreffile).getroot()
                        refmaterial = xmlref.find('.//Material[@Name="%s"]' % mat.name)
                        if not refmaterial is None:
                            refmatpath = refmaterial.find('.//Parameter[@Name="%s"]' % texTypeS)
                            if not refmatpath is None:
                                filepath = refmatpath.text
                        else:
                            if not texType.name == 'ColorMetal' and not cmpath is None:
                                textfilepath = None
                                if texType.name == 'NormalGloss':
                                    textfilepath =  (cmpath + "ng.dds")
                                if texType.name == 'AddMaps':
                                    textfilepath =  (cmpath + "add.dds")
                                if texType.name == 'Alphamask':
                                    if not technique_param == 'GLASS' and not technique_param == 'MESH':
                                        textfilepath =  (cmpath + "alphamask.dds")
                                    else:
                                        textfilepath = None
                                
                                if not textfilepath is None:
                                    exists = Path(textfilepath)
                                    if exists.is_file():
                                        filepath = textfilepath
                                        break
                                    else:
                                        filepath = None
                                else:
                                    filepath = None
                
            if not filepath is None and not technique_param == "GLASS":
                derivedPath = derive_texture_path(settings, filepath)
                #print("TexPath: "+derivedPath)
                if (BAD_PATH.search(derivedPath)):
                    settings.error("The %s texture of material '%s' exports with the non-portable path: '%s'. "
                                   "Consult the documentation on texture-paths."
                                   % (texType.name, mat.name, derivedPath), file=file, node=node)
                if technique_param == "MESH" and texType.name == "Alphamask":
                    e.append(ElementTree.Comment("material has no %sTexture" % texType.name))
                elif checked == 1:
                    param(texType.name + "Texture", derivedPath)
                else:
                    e.append(ElementTree.Comment("material has no %sTexture" % texType.name))
            elif not technique_param == "GLASS":
                if texType.name != 'Alphamask':
                    e.append(ElementTree.Comment("material has no %sTexture" % texType.name))
                elif technique_param != 'MESH':
                    e.append(ElementTree.Comment("material has no %sTexture" % texType.name))

        return e
    

def materialref_xml(settings, matref, file=None, node=None):
    d = data(matref)
    m = SEMaterialInfo(matref)
    technique_param = _material_technique(d.technique)
    matreffound = None
    ismatref = True
    
    if technique_param != "AUTO":
        ismatref = None
    if m.images.get(TextureType.ColorMetal, None):
        ismatref = None
    if m.images.get(TextureType.NormalGloss, None) and d.usetextureng:
        ismatref = None
    if m.images.get(TextureType.AddMaps, None) and d.usetextureadd:
        ismatref = None
    if m.images.get(TextureType.Alphamask, None) and d.usetexturealpha:
        ismatref = None
    
    if not ismatref is None:
        for xmlreffile in settings.matreffiles:
            if not xmlreffile: #don't want it failing if we don't have a file assigned
                xmlreffile = None

            if not xmlreffile is None:
                xmlref = ElementTree.parse(xmlreffile).getroot()
                refmaterial = xmlref.find('.//Material[@Name="%s"]' % matref.name)
                if not refmaterial is None:
                    matreffound = True
                    break
        
        if not matreffound is None:
            e = ElementTree.Element("MaterialRef", Name=matref.name)
        else:
            e = ElementTree.Comment("material %s can't be found" % matref.name)
        
        return e

def lod_xml(settings, lodMwmFile: str, lodDistance: int, renderQualities:iter=None):
    e = ElementTree.Element("LOD")
    attrib = OrderedDict()
    attrib['Distance'] = str(lodDistance)
    if not renderQualities is None:
        attrib['RenderQuality'] = ', '.join(renderQualities)
    e.attrib = attrib

    em = ElementTree.SubElement(e, "Model")
    try:
        filePath = os.path.relpath(os.path.join(settings.outputDir, lodMwmFile), settings.baseDir)
    except ValueError:
        filePath = settings.template(settings.names.modelpath, modelfile=os.path.basename(lodMwmFile))
    em.text = filePath
    return e

def mwmbuilder_xml(settings, material_elements, materialref_elements, lod_elements, rescale_factor: float = 1, rotation_y: float = 0):
    d = data(settings.scene)
    e = ElementTree.Element("Model", Name=settings.blockname)

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value

    param("RescaleFactor", str(round(rescale_factor, 3)))
    param("RescaleToLengthInMeters", "false")
    param("Centered", "false")
    if rotation_y:
        param("RotationY", str(round(rotation_y, 3)))

    for mat in material_elements:
        if not mat is None:
            e.append(mat)
    
    for matref in materialref_elements:
        if not matref is None:
            e.append(matref)

    for lod in lod_elements:
        e.append(lod)

    # TODO other mwmbuilder.xml parameters:

    # <Parameter Name="PatternScale">4</Parameter>
    # <BoneGridSize>2.5</BoneGridSize>
    # <BoneMapping>
    #    <Bone X="0" Y="0" Z="0" />

    return e
from .tool_registry import tool
import urllib.request
import json
from qgis.core import QgsMapLayer
from qgis.core import (QgsProject, QgsProcessingAlgRunnerTask,
                       QgsProcessingContext, QgsProcessingFeedback,
                       QgsField, QgsExpression, QgsExpressionContext,
                       QgsExpressionContextUtils,QgsCoordinateReferenceSystem, 
                       QgsVectorFileWriter,QgsWkbTypes, QgsCoordinateTransform)
from qgis.core import QgsPointXY, QgsGeometry, QgsFeature, QgsVectorLayer  
from qgis.core import (QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol, QgsSingleSymbolRenderer)
from qgis.core import QgsFillSymbol                     
from qgis import processing
from PyQt5.QtCore import QVariant
import re
import os
import tempfile
import logging


# 创建工具专用的 logger
tool_logger = logging.getLogger("GeoAssistant.Tools")

# 执行QGIS算法的基础函数
def run_qgis_algorithm(alg_name: str, params: dict) -> str:
    """执行QGIS算法的基础函数"""
    try:
        # 确保使用临时输出
        if 'OUTPUT' not in params:
            params['OUTPUT'] = 'memory:'

        result = processing.run(alg_name, params)

        # 自动将结果添加到地图
        if 'OUTPUT' in result:
            output_layer = result['OUTPUT']

            # 生成有意义的图层名称
            input_name = params.get('INPUT', '').name() if hasattr(params.get('INPUT', ''), 'name') else "结果"
            alg_name_short = alg_name.split(':')[-1]
            output_layer.setName(f"{input_name}_{alg_name_short}")

            QgsProject.instance().addMapLayer(output_layer)
            return f"操作成功！新图层已添加到地图: {output_layer.name()}"

        return "操作成功！"
    except Exception as e:
        return f"执行错误: {str(e)}"


# 智能查找图层函数
def find_layer(layer_name: str):
    """
    智能查找QGIS工程中的图层，支持多种匹配方式

    Parameters:
        layer_name: 用户输入的图层名称（可能不完整或不精确）

    Returns:
        QgsMapLayer对象 或 None（如果找不到）
    """
    project = QgsProject.instance()
    all_layers = project.mapLayers().values()

    # 1. 精确匹配（区分大小写）
    for layer in all_layers:
        if layer.name() == layer_name:
            return layer

    # 2. 精确匹配（不区分大小写）
    for layer in all_layers:
        if layer.name().lower() == layer_name.lower():
            return layer

    # 3. 部分匹配（包含关系）
    candidates = []
    for layer in all_layers:
        if layer_name.lower() in layer.name().lower():
            candidates.append(layer)

    if candidates:
        # 优先选择名称更接近的图层
        if len(candidates) == 1:
            return candidates[0]

        # 如果有多个候选，返回名称最长的（通常更具体）
        return max(candidates, key=lambda l: len(l.name()))

    # 4. 返回所有可用图层信息（用于错误提示）
    available_layers = [layer.name() for layer in all_layers]
    return None, available_layers


# 创建图层的临时副本
def create_temp_copy(layer: QgsVectorLayer) -> QgsVectorLayer:
    """创建图层的临时副本，避免修改原始图层"""
    # 在内存中创建副本
    temp_layer = QgsVectorLayer(
        layer.source(),
        f"{layer.name()}_副本",
        layer.providerType()
    )

    if not temp_layer.isValid():
        # 如果内存副本失败，使用临时文件
        temp_file = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False).name
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            temp_file,
            QgsProject.instance().transformContext(),
            options
        )
        temp_layer = QgsVectorLayer(temp_file, f"{layer.name()}_副本", "ogr")

    return temp_layer


# 获取当前工程信息工具
@tool(name="list_project_layers")
def list_project_layers() -> str:
    """列出当前QGIS工程中的所有图层

    返回当前工程中所有加载图层的名称、类型和要素数量信息

    Returns:
        包含所有图层信息的格式化字符串
    """
    layers = QgsProject.instance().mapLayers().values()
    if not layers:
        return "当前工程中没有加载任何图层"

    layer_info = []
    for layer in layers:
        # 处理矢量图层和栅格图层的差异
        if layer.type() == QgsMapLayer.VectorLayer:
            info = f"{layer.name()} (矢量图层, {layer.featureCount()}个要素)"
        elif layer.type() == QgsMapLayer.RasterLayer:
            info = f"{layer.name()} (栅格图层)"
        else:
            info = f"{layer.name()} (未知图层类型)"
        
        layer_info.append(info)

    return "当前工程中的图层:\n" + "\n".join(layer_info)


# 缓冲区分析工具
@tool(name="create_buffer")
def create_buffer(input_layer: str, distance: float, unit: str = "米", dissolve: bool = False) -> str:
    """创建面要素的缓冲区

    在输入图层周围创建指定距离的缓冲区区域，可选择溶解相邻缓冲区

    Parameters:
        - input_layer: 输入图层的名称 (需已加载到QGIS中)
        - distance: 缓冲距离
        - unit: 距离单位 (支持: 米, 千米, 公里, 英尺, 英里, 码, 海里, 度; 默认: 米)
        - dissolve: 是否溶解相邻缓冲区 (默认False)
    """
    try:    
        # 查找原始图层
        orig_layer = find_layer(input_layer)
        if isinstance(orig_layer, tuple) or not orig_layer:
            return f"错误：找不到图层 '{input_layer}'"
        
        # 检查坐标系类型
        crs = orig_layer.crs()
        is_geographic = crs.isGeographic()  # 判断是否为地理坐标系（经纬度）
        tool_logger.info(f"图层坐标系: {crs.authid()}, 地理坐标系: {is_geographic}")
        
        # 单位转换因子（转换为米）
        unit_conversion = {
            "米": 1.0,
            "千米": 1000.0,
            "公里": 1000.0,
            "英尺": 0.3048,
            "英里": 1609.344,
            "码": 0.9144,
            "海里": 1852.0,
            "度": 111319.49  # 1度约等于111公里
        }
        
        # 验证单位
        if unit.lower() not in [u.lower() for u in unit_conversion.keys()]:
            valid_units = ", ".join(unit_conversion.keys())
            return f"错误: 不支持的单位 '{unit}'。支持的单元: {valid_units}"
        
        # 获取正确的单位键（不区分大小写）
        matched_unit = next(u for u in unit_conversion.keys() if u.lower() == unit.lower())
        
        # 转换为米
        distance_meters = distance * unit_conversion[matched_unit]
        tool_logger.info(f"转换距离: {distance} {matched_unit} = {distance_meters} 米")
        
        # 地理坐标系需要特殊处理（将米转换为度）
        if is_geographic:
            # 计算图层中心点
            extent = orig_layer.extent()
            center_x = (extent.xMinimum() + extent.xMaximum()) / 2
            center_y = (extent.yMinimum() + extent.yMaximum()) / 2
            
            # 创建临时点图层用于计算转换因子
            point_layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "temp_point", "memory")
            if not point_layer.isValid():
                return "错误：无法创建临时点图层用于坐标转换"
            
            # 创建点要素
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(center_x, center_y)))
            point_layer.dataProvider().addFeatures([feature])
            point_layer.updateExtents()
            
            # 创建临时投影坐标系（Web Mercator）
            target_crs = QgsCoordinateReferenceSystem("EPSG:3857")
            
            # 计算米到度的转换因子（近似值）
            transform = QgsCoordinateTransform(crs, target_crs, QgsProject.instance())
            point_target = transform.transform(QgsPointXY(center_x, center_y))
            point_target_offset = transform.transform(QgsPointXY(center_x, center_y + 1))
            meters_per_degree = abs(point_target_offset.y() - point_target.y())
            
            if meters_per_degree <= 0:
                return "错误：无法计算有效的单位转换因子"
            
            # 转换距离单位
            distance_degrees = distance_meters / meters_per_degree
            tool_logger.info(f"转换距离单位: {distance_meters}米 ≈ {distance_degrees}° (在纬度方向)")
            
            # 使用转换后的距离
            actual_distance = distance_degrees
        else:
            # 投影坐标系直接使用米为单位
            actual_distance = distance_meters
        
        # 创建副本
        layer = create_temp_copy(orig_layer)
        
        # 设置缓冲区参数
        params = {
            'INPUT': layer,
            'DISTANCE': actual_distance,
            'DISSOLVE': dissolve,
            'OUTPUT': 'memory:Buffer'
        }
        
        # 执行缓冲区算法
        result = run_qgis_algorithm("native:buffer", params)
        
        # 添加成功信息（如果包含新图层）
        if "新图层已添加到地图" in result:
            # 查找新添加的缓冲区图层
            buffer_layers = [lyr for lyr in QgsProject.instance().mapLayers().values() 
                            if "Buffer" in lyr.name()]
            if buffer_layers:
                buffer_layer = buffer_layers[0]
                
                # 设置图层名称（包含距离和单位）
                buffer_layer.setName(f"{orig_layer.name()}_{distance}{matched_unit}缓冲区")
                
                # 设置智能样式
                if buffer_layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    # 面图层样式
                    symbol = QgsFillSymbol.createSimple({
                        'color': '50,150,200,150',
                        'outline_color': '0,0,0'
                    })
                    buffer_layer.renderer().setSymbol(symbol)
                    buffer_layer.triggerRepaint()
        
        return result.replace("Buffer", f"{distance}{matched_unit}缓冲区")
        
    except Exception as e:
        import traceback
        tool_logger.error(f"创建缓冲区失败: {str(e)}\n{traceback.format_exc()}")
        return f"创建缓冲区时出错: {str(e)}"


# 矢量叠加分析工具
@tool(name="vector_overlay")
def vector_overlay(input_layer: str, overlay_layer: str, overlay_type: str, auto_fix_geometries: bool = True) -> str:
    """执行矢量叠加分析
    
    对两个输入图层执行指定的叠加分析操作（交集、并集、差集、对称差集、裁剪）
    自动修复无效几何图形以确保分析成功

    Parameters:
        - input_layer: 输入图层的名称
        - overlay_layer: 叠加图层的名称
        - overlay_type: 叠加分析类型，支持:
            'intersection'(交集),
            'union'(并集),
            'difference'(差集),
            'symmetrical_difference'(对称差集),
            'clip'(裁剪)
        - auto_fix_geometries: 是否自动修复无效几何（默认True）
    """
    try:
        tool_logger.info(f"开始执行叠加分析: {overlay_type} ({input_layer} 和 {overlay_layer})")
        
        # 查找输入图层
        input_orig_layer = find_layer(input_layer)
        if not input_orig_layer or isinstance(input_orig_layer, tuple):
            return f"错误：找不到输入图层 '{input_layer}'"
        if not isinstance(input_orig_layer, QgsVectorLayer):
            return f"错误：输入图层 '{input_layer}' 不是矢量图层"

        # 查找叠加图层
        overlay_orig_layer = find_layer(overlay_layer)
        if not overlay_orig_layer or isinstance(overlay_orig_layer, tuple):
            return f"错误：找不到叠加图层 '{overlay_layer}'"
        if not isinstance(overlay_orig_layer, QgsVectorLayer):
            return f"错误：叠加图层 '{overlay_layer}' 不是矢量图层"
        
        # 自动修复几何（如果需要）
        if auto_fix_geometries:
            # 检查输入图层是否需要修复
            input_needs_fix = any(not f.geometry().isGeosValid() 
                                 for f in input_orig_layer.getFeatures())
            
            # 检查叠加图层是否需要修复
            overlay_needs_fix = any(not f.geometry().isGeosValid() 
                                   for f in overlay_orig_layer.getFeatures())
            
            if input_needs_fix or overlay_needs_fix:
                # 修复输入图层
                if input_needs_fix:
                    fix_result = fix_geometries(input_layer)
                    if "新图层已添加到地图" in fix_result:
                        fixed_name = re.search(r"新图层已添加到地图: (.+?)$", fix_result).group(1)
                        input_orig_layer = find_layer(fixed_name)
                        tool_logger.info(f"使用修复后的输入图层: {fixed_name}")
                
                # 修复叠加图层
                if overlay_needs_fix:
                    fix_result = fix_geometries(overlay_layer)
                    if "新图层已添加到地图" in fix_result:
                        fixed_name = re.search(r"新图层已添加到地图: (.+?)$", fix_result).group(1)
                        overlay_orig_layer = find_layer(fixed_name)
                        tool_logger.info(f"使用修复后的叠加图层: {fixed_name}")

        # 检查坐标系统一致性
        input_crs = input_orig_layer.crs()
        overlay_crs = overlay_orig_layer.crs()
        
        if input_crs != overlay_crs:
            tool_logger.warning(f"坐标系统不一致: {input_crs.authid()} vs {overlay_crs.authid()}")
            
            # 自动重投影到输入图层的坐标系统
            params = {
                'INPUT': overlay_orig_layer,
                'TARGET_CRS': input_crs,
                'OUTPUT': 'memory:'
            }
            reproject_result = processing.run("native:reprojectlayer", params)
            if 'OUTPUT' not in reproject_result or not reproject_result['OUTPUT'].isValid():
                return "错误：无法重投影叠加图层"
                
            overlay_orig_layer = reproject_result['OUTPUT']
            overlay_orig_layer.setName(f"{overlay_layer}_重投影")
            tool_logger.info(f"已重投影叠加图层到 {input_crs.authid()}")

        # 创建图层副本
        input_layer_copy = create_temp_copy(input_orig_layer)
        overlay_layer_copy = create_temp_copy(overlay_orig_layer)

        # 映射叠加类型到QGIS算法
        valid_types = {
            'intersection': "native:intersection",
            'union': "native:union",
            'difference': "native:difference",
            'symmetrical_difference': "native:symmetricaldifference",
            'clip': "native:clip"
        }

        # 中文名称映射（用于图层命名）
        type_names = {
            'intersection': "交集",
            'union': "并集",
            'difference': "差集",
            'symmetrical_difference': "对称差集",
            'clip': "裁剪"
        }
        
        if overlay_type not in valid_types:
            return f"错误：无效的叠加类型 '{overlay_type}'。可用类型: {', '.join(valid_types.keys())}"
        
        alg_name = valid_types[overlay_type]
        type_name = type_names[overlay_type]

        # 根据叠加类型设置参数
        if overlay_type == 'clip':
            params = {
                'INPUT': input_layer_copy,
                'OVERLAY': overlay_layer_copy,
                'OUTPUT': 'memory:'
            }
        else:
            params = {
                'INPUT': input_layer_copy,
                'OVERLAY': overlay_layer_copy,
                'OUTPUT': 'memory:'
            }
  
        # 执行叠加分析
        result = processing.run(alg_name, params)
        if not result or 'OUTPUT' not in result:
            return f"执行 {overlay_type} 叠加分析失败"
        
        # 获取结果图层
        output_layer = result['OUTPUT']
        if not output_layer.isValid():
            return f"错误：生成的{overlay_type}结果图层无效"
            
        if output_layer.featureCount() == 0:
            return f"注意：执行 {overlay_type} 叠加分析后没有生成要素" 
        
        # 生成有意义的图层名称
        output_name = f"{input_orig_layer.name()}_{type_name}_{overlay_orig_layer.name()}"
        if len(output_name) > 50:  # 防止图层名称过长
            output_name = f"{type_name}_{input_orig_layer.name()[:20]}_{overlay_orig_layer.name()[:20]}"

        
        # 设置智能样式
        geom_type = output_layer.geometryType()
        
        if geom_type == QgsWkbTypes.PointGeometry:
            # 点图层样式
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '200,50,50',
                'size': '3'
            })
        elif geom_type == QgsWkbTypes.LineGeometry:
            # 线图层样式
            symbol = QgsLineSymbol.createSimple({
                'color': '50,50,200',
                'width': '1'
            })
        else:
            # 面图层样式
            color_map = {
                'intersection': '50,200,50,150',
                'difference': '200,50,50,150',
                'union': '50,50,200,150',
                'symmetrical_difference': '200,50,200,150',
                'clip': '255,255,0,150'
            }
            symbol = QgsFillSymbol.createSimple({
                'color': color_map.get(overlay_type, '100,100,100,150'),
                'outline_color': '0,0,0'
            })
        
        output_layer.renderer().setSymbol(symbol)
        output_layer.triggerRepaint()
        
        # 添加到工程
        output_layer.setName(output_name)
        QgsProject.instance().addMapLayer(output_layer)
        tool_logger.info(f"叠加分析成功完成: {output_name} ({output_layer.featureCount()}个要素)")
        
        return f"成功完成 {overlay_type} 叠加分析！新图层已添加到地图: {output_name}"
    except Exception as e:
        import traceback
        tool_logger.error(f"矢量叠加分析失败: {str(e)}\n{traceback.format_exc()}")
        return f"矢量叠加分析失败: {str(e)}"


# 几何修复工具
@tool(name="fix_geometries")
def fix_geometries(input_layer: str, method: str = "structure") -> str:
    """修复矢量图层中的无效几何图形
    
    检测并修复图层中的无效几何（如自相交、孔洞等），确保几何有效性

    Parameters:
        - input_layer: 输入图层的名称
        - method: 修复方法，可选值:
            'structure' - 结构修复（默认，保持原始形状）
            'buffer' - 缓冲区修复（更彻底但可能改变形状）
    """
    try:
        # 查找原始图层
        orig_layer = find_layer(input_layer)
        if not orig_layer or isinstance(orig_layer, tuple):
            return f"错误：找不到图层 '{input_layer}'"
        if not isinstance(orig_layer, QgsVectorLayer):
            return f"错误：图层 '{input_layer}' 不是矢量图层"
        
        # 检查无效几何数量
        invalid_count = 0
        for feature in orig_layer.getFeatures():
            if not feature.geometry().isGeosValid():
                invalid_count += 1
        
        if invalid_count == 0:
            return f"图层 '{input_layer}' 没有无效几何图形，无需修复"
        
        tool_logger.info(f"发现 {invalid_count} 个无效几何图形，开始修复...")
        
        # 映射修复方法
        method_map = {
            "structure": 0,  # 结构修复
            "buffer": 1     # 缓冲区修复
        }
        
        if method not in method_map:
            return f"错误：无效的修复方法 '{method}'。可用方法: structure, buffer"
        
        # 执行几何修复
        params = {
            'INPUT': orig_layer,
            'METHOD': method_map[method],
            'OUTPUT': 'memory:'
        }
        
        result = processing.run("native:fixgeometries", params)
        if not result or 'OUTPUT' not in result:
            return "几何修复失败"
        
        output_layer = result['OUTPUT']
        if not output_layer.isValid():
            return "错误：生成的修复图层无效"
        
        # 设置图层名称
        output_name = f"{orig_layer.name()}_修复几何"
        output_layer.setName(output_name)
        
        # 设置样式（黄色表示修复结果）
        geom_type = output_layer.geometryType()
        if geom_type == QgsWkbTypes.PolygonGeometry:
            symbol = QgsFillSymbol.createSimple({
                'color': '255,255,0,150',
                'outline_color': '0,0,0'
            })
            output_layer.renderer().setSymbol(symbol)
        elif geom_type == QgsWkbTypes.LineGeometry:
            symbol = QgsLineSymbol.createSimple({
                'color': '255,255,0',
                'width': '1.5'
            })
            output_layer.renderer().setSymbol(symbol)
        elif geom_type == QgsWkbTypes.PointGeometry:
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '255,255,0',
                'size': '3'
            })
            output_layer.renderer().setSymbol(symbol)
        
        output_layer.triggerRepaint()
        
        # 添加到工程
        QgsProject.instance().addMapLayer(output_layer)
        
        # 验证修复结果
        fixed_invalid = 0
        for feature in output_layer.getFeatures():
            if not feature.geometry().isGeosValid():
                fixed_invalid += 1
        
        if fixed_invalid > 0:
            return f"警告：修复后仍有 {fixed_invalid} 个无效几何。建议尝试缓冲区修复方法。"
        
        return f"成功修复几何图形！新图层已添加到地图: {output_name}"
    
    except Exception as e:
        import traceback
        tool_logger.error(f"几何修复失败: {str(e)}\n{traceback.format_exc()}")
        return f"几何修复失败: {str(e)}"


# 重采样工具
@tool(name="resample_raster")
def resample_raster(input_raster: str, target_resolution: float, resampling_method: str = "nearest") -> str:
    """对栅格图层进行重采样，改变其空间分辨率
    
    将栅格图层重采样到指定的分辨率，支持多种重采样方法

    Parameters:
        - input_raster: 输入栅格图层的名称
        - target_resolution: 目标分辨率（像元大小），单位与输入栅格相同
        - resampling_method: 重采样方法，可选值:
            'nearest' - 最近邻法（默认，适用于分类数据）
            'bilinear' - 双线性插值（适用于连续数据）
            'cubic' - 立方卷积插值（适用于连续数据）
            'average' - 平均值法（适用于连续数据）
    """
    try:
        # 查找原始栅格图层
        orig_raster = find_layer(input_raster)
        if isinstance(orig_raster, tuple) or not orig_raster:
            return f"错误：找不到栅格图层 '{input_raster}'"
        
        if orig_raster.type() != QgsMapLayer.RasterLayer:
            return f"错误：图层 '{input_raster}' 不是栅格图层"
        
        # 获取原始分辨率信息
        original_resolution = orig_raster.rasterUnitsPerPixelX()
        tool_logger.info(f"原始分辨率: {original_resolution}, 目标分辨率: {target_resolution}")
        
        # 验证目标分辨率
        if target_resolution <= 0:
            return "错误：目标分辨率必须大于0"
        
        # 映射重采样方法到GDAL方法编号
        method_map = {
            'nearest': 0,      # 最近邻法
            'bilinear': 1,     # 双线性插值
            'cubic': 2,        # 立方卷积
            'average': 3,      # 平均值法
        }
        
        if resampling_method not in method_map:
            valid_methods = ", ".join(method_map.keys())
            return f"错误：无效的重采样方法 '{resampling_method}'。可用方法: {valid_methods}"
        
        method_code = method_map[resampling_method]
        method_names = {
            'nearest': "最近邻",
            'bilinear': "双线性",
            'cubic': "立方卷积", 
            'average': "平均值"
        }
        method_name = method_names[resampling_method]
        
        # 计算输出尺寸
        original_extent = orig_raster.extent()
        width = orig_raster.width()
        height = orig_raster.height()
        
        # 根据分辨率比例计算新尺寸
        resolution_ratio = original_resolution / target_resolution
        new_width = int(width * resolution_ratio)
        new_height = int(height * resolution_ratio)
        
        tool_logger.info(f"原始尺寸: {width}x{height}, 新尺寸: {new_width}x{new_height}")
        
        # 设置重采样参数
        params = {
            'INPUT': orig_raster,
            'TARGET_RESOLUTION': target_resolution,
            'RESAMPLING': method_code,
            'OUTPUT': 'memory:'
        }
        
        # 执行重采样算法
        result = processing.run("gdal:warpreproject", params)
        if not result or 'OUTPUT' not in result:
            return "重采样失败"
        
        output_raster = result['OUTPUT']
        if not output_raster.isValid():
            return "错误：生成的重采样图层无效"
        
        # 设置有意义的图层名称
        output_name = f"{orig_raster.name()}_重采样_{target_resolution}_{method_name}"
        output_raster.setName(output_name)
        
        # 添加到工程
        QgsProject.instance().addMapLayer(output_raster)
        
        # 获取重采样后的实际分辨率
        actual_resolution = output_raster.rasterUnitsPerPixelX()
        
        tool_logger.info(f"重采样成功完成: {output_name} (分辨率: {actual_resolution})")
        
        return f"重采样成功！新图层已添加到地图: {output_name}\n" \
               f"原始分辨率: {original_resolution:.4f} → 目标分辨率: {actual_resolution:.4f}\n" \
               f"重采样方法: {method_name}\n" \
               f"输出尺寸: {new_width}×{new_height} 像元"
        
    except Exception as e:
        import traceback
        tool_logger.error(f"栅格重采样失败: {str(e)}\n{traceback.format_exc()}")
        return f"栅格重采样失败: {str(e)}"


# 获取栅格信息工具
@tool(name="get_raster_info") 
def get_raster_info(input_raster: str) -> str:
    """获取栅格图层的详细信息
    
    返回栅格图层的分辨率、尺寸、波段数等基本信息

    Parameters:
        - input_raster: 输入栅格图层的名称
    """
    try:
        # 查找栅格图层
        raster_layer = find_layer(input_raster)
        if isinstance(raster_layer, tuple) or not raster_layer:
            return f"错误：找不到栅格图层 '{input_raster}'"
        
        if raster_layer.type() != QgsMapLayer.RasterLayer:
            return f"错误：图层 '{input_raster}' 不是栅格图层"
        
        # 获取栅格信息
        extent = raster_layer.extent()
        width = raster_layer.width()
        height = raster_layer.height()
        resolution_x = raster_layer.rasterUnitsPerPixelX()
        resolution_y = raster_layer.rasterUnitsPerPixelY()
        band_count = raster_layer.bandCount()
        crs = raster_layer.crs().authid()
        
        info = f"栅格图层 '{input_raster}' 的详细信息:\n"
        info += f"• 空间参考: {crs}\n"
        info += f"• 像元大小: {resolution_x:.6f} × {resolution_y:.6f}\n"
        info += f"• 像元数量: {width} × {height}\n"
        info += f"• 地理范围: \n"
        info += f"  东: {extent.xMaximum():.6f}\n"
        info += f"  西: {extent.xMinimum():.6f}\n"  
        info += f"  北: {extent.yMaximum():.6f}\n"
        info += f"  南: {extent.yMinimum():.6f}\n"
        info += f"• 波段数量: {band_count}\n"
        
        # 获取波段信息
        for i in range(1, band_count + 1):
            stats = raster_layer.dataProvider().bandStatistics(i)
            info += f"• 波段 {i}: 最小值={stats.minimumValue:.2f}, 最大值={stats.maximumValue:.2f}\n"
        
        return info
        
    except Exception as e:
        import traceback
        tool_logger.error(f"获取栅格信息失败: {str(e)}\n{traceback.format_exc()}")
        return f"获取栅格信息失败: {str(e)}"


# 高亮要素工具
@tool(name="highlight_features")
def highlight_features(input_layer: str, expression: str, style: str = "red") -> str:
    """高亮显示图层中符合指定条件的要素
    
    根据提供的属性表达式筛选要素，并创建包含这些要素的新图层进行高亮显示。
    支持自定义高亮颜色样式。
    
    Parameters:
        - input_layer: 输入图层的名称
        - expression: 属性表达式 (例如："population > 1000000" 或 "name LIKE '武汉%'")
        - style: 高亮样式 (可选值: 'red', 'blue', 'green', 'yellow', 'purple')，默认为红色
    """
    try:
        # 查找原始图层
        orig_layer = find_layer(input_layer)
        if isinstance(orig_layer, tuple) or not orig_layer:
            return f"错误：找不到图层 '{input_layer}'"
        
        if not isinstance(orig_layer, QgsVectorLayer):
            return f"错误：图层 '{input_layer}' 不是矢量图层"
        
        # 验证表达式
        qgs_expr = QgsExpression(expression)
        if qgs_expr.hasParserError():
            return f"表达式错误: {qgs_expr.parserErrorString()}"
        
        # 测试表达式是否有效
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(orig_layer))
        if not qgs_expr.prepare(context):
            return f"表达式准备失败: {qgs_expr.evalErrorString()}"
        
        # 创建新图层
        output_name = f"{orig_layer.name()}_高亮要素"
        
        # 使用提取工具创建新图层
        params = {
            'INPUT': orig_layer,
            'EXPRESSION': expression,
            'OUTPUT': 'memory:'
        }
        
        result = processing.run("native:extractbyexpression", params)
        if not result or 'OUTPUT' not in result:
            return "高亮要素失败：无法创建输出图层"
        
        output_layer = result['OUTPUT']
        if output_layer.featureCount() == 0:
            return f"没有找到符合条件的要素 (表达式: {expression})"
        
        # 根据图层几何类型设置高亮样式
        geom_type = output_layer.geometryType()
        
        # 预定义颜色方案
        color_schemes = {
            'red': {
                'point': {'color': '255,0,0', 'size': '3', 'name': 'circle'},
                'line': {'color': '255,0,0', 'width': '2.0'},
                'polygon': {'color': '255,0,0,100', 'outline': '255,0,0'}
            },
            'blue': {
                'point': {'color': '0,0,255', 'size': '3', 'name': 'circle'},
                'line': {'color': '0,0,255', 'width': '2.0'},
                'polygon': {'color': '0,0,255,100', 'outline': '0,0,255'}
            },
            'green': {
                'point': {'color': '0,255,0', 'size': '3', 'name': 'circle'},
                'line': {'color': '0,255,0', 'width': '2.0'},
                'polygon': {'color': '0,255,0,100', 'outline': '0,255,0'}
            },
            'yellow': {
                'point': {'color': '255,255,0', 'size': '3', 'name': 'circle'},
                'line': {'color': '255,255,0', 'width': '2.0'},
                'polygon': {'color': '255,255,0,100', 'outline': '255,255,0'}
            },
            'purple': {
                'point': {'color': '255,0,255', 'size': '3', 'name': 'circle'},
                'line': {'color': '255,0,255', 'width': '2.0'},
                'polygon': {'color': '255,0,255,100', 'outline': '255,0,255'}
            }
        }
        
        # 获取选择的颜色方案，默认为红色
        scheme = color_schemes.get(style.lower(), color_schemes['red'])
        
        # 根据几何类型创建符号
        if geom_type == QgsWkbTypes.PointGeometry:
            # 点图层样式
            symbol = QgsMarkerSymbol.createSimple(scheme['point'])
        elif geom_type == QgsWkbTypes.LineGeometry:
            # 线图层样式
            symbol = QgsLineSymbol.createSimple(scheme['line'])
        else:
            # 面图层样式
            symbol = QgsFillSymbol.createSimple({
                'color': scheme['polygon']['color'],
                'outline_color': scheme['polygon']['outline'],
                'outline_width': '0.5'
            })
        
        # 设置图层渲染器
        renderer = QgsSingleSymbolRenderer(symbol)
        output_layer.setRenderer(renderer)
        output_layer.triggerRepaint()
        
        # 添加到工程
        output_layer.setName(output_name)
        QgsProject.instance().addMapLayer(output_layer)
        
        return f"已高亮显示符合条件的要素！新图层已添加到地图: {output_name}"
    except Exception as e:
        import traceback
        tool_logger.error(f"高亮要素失败: {str(e)}\n{traceback.format_exc()}")
        return f"高亮要素失败: {str(e)}"
        
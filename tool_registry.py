from typing import Callable, Any, Dict, get_type_hints, Optional
from dataclasses import dataclass
import inspect
from typing import get_origin, get_args, Literal


# 工具元数据类
@dataclass
class Tool:
    """表示一个可用的地理处理工具"""
    name: str
    description: str
    func: Callable[..., str]
    parameters: Dict[str, Dict[str, str]]

    def __call__(self, *args, **kwargs) -> str:
        return self.func(*args, **kwargs)


# 工具解析方法
def parse_docstring_params(docstring: str) -> Dict[str, str]:
    """从文档字符串提取参数描述"""
    if not docstring:
        return {}

    params = {}
    lines = docstring.split('\n')
    in_params = False
    current_param = None

    for line in lines:
        line = line.strip()
        if line.startswith('Parameters:'):
            in_params = True
        elif in_params:
            if line.startswith('-') or line.startswith('*'):
                current_param = line.lstrip('- *').split(':')[0].strip()
                params[current_param] = line.lstrip('- *').split(':')[1].strip()
            elif current_param and line:
                params[current_param] += ' ' + line.strip()
            elif not line:
                in_params = False

    return params


def get_type_description(type_hint: Any) -> str:
    """获取类型提示的人类可读描述"""
    origin = get_origin(type_hint)  # 标准方法获取类型原始类
    if origin is not None:
        if origin is Literal:  # 直接比较是否为Literal类型
            args = get_args(type_hint)
            return f"one of {args}"
    # 其他泛型类型可在此扩展
    return getattr(type_hint, "__name__", str(type_hint))


# 工具装饰器
def tool(name: Optional[str] = None) -> Callable[[Callable[..., str]], Tool]:
    """装饰器工厂，将函数转换为Tool对象"""
    def decorator(func: Callable[..., str]) -> Tool:
        tool_name = name or func.__name__
        description = inspect.getdoc(func) or "No description available"

        type_hints = get_type_hints(func)
        param_docs = parse_docstring_params(description)
        sig = inspect.signature(func)

        params = {}
        for param_name, param in sig.parameters.items():
            params[param_name] = {
                "type": get_type_description(type_hints.get(param_name, Any)),
                "description": param_docs.get(param_name, "No description available")
            }

        return Tool(
            name=tool_name,
            description=description.split('\n\n')[0],
            func=func,
            parameters=params
        )

    return decorator

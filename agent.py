from typing import Dict, List, Any, Callable
from .tool_registry import Tool
import openai
import json
import re  # 正则模块
import threading
import logging


class Agent:
    """AI代理核心类，处理用户查询和工具调用"""
    def __init__(self):
        """初始化Agent"""
        # 使用本地部署的大模型
        # self.base_url = "http://127.0.0.1:11434/v1"
        # self.api_key = "ollama"
        # self.model = "deepseek-r1:7b"
        # self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        # 使用网络的API密钥
        self.base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.api_key="sk-856c716dfcea47a99f1494d3e05e58fa"
        self.model="deepseek-v3.1"
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

        # 工具注册表
        self.tools: Dict[str, Tool] = {}

        # 日志配置
        self.logger = logging.getLogger("GeoAssistant.Agent")

    # 工具管理方法
    def add_tool(self, tool: Tool) -> None:
        """添加工具到代理"""
        self.tools[tool.name] = tool

    def get_available_tools(self) -> List[str]:
        """获取可用工具列表"""
        return [f"{tool.name}: {tool.description}" for tool in self.tools.values()]

    def use_tool(self, tool_name: str, **kwargs: Any) -> str:
        """使用指定工具并返回结果"""
        if tool_name not in self.tools:
            raise ValueError(f"Tool '{tool_name}' not found. Available tools: {list(self.tools.keys())}")
        tool = self.tools[tool_name]
        return tool.func(**kwargs)

    # 查询处理方法
    def create_system_prompt(self) -> str:
        """创建系统提示，定义代理行为和工具"""
        tools_json = {
            "role": "AI Assistant",
            "capabilities": [
                "Using provided tools to help users when necessary",
                "Responding directly without tools for questions that don't require tool usage",
                "Planning efficient tool usage sequences"
            ],
            "instructions": [
                "Use tools only when they are necessary for the task",
                "If a query can be answered directly, respond with a simple message instead of using tools",
                "When tools are needed, plan their usage efficiently to minimize tool calls"
            ],
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        name: {
                            "type": info["type"],
                            "description": info["description"]
                        }
                        for name, info in tool.parameters.items()
                    }
                }
                for tool in self.tools.values()
            ],
            "response_format": {
                "type": "json",
                "schema": {
                    "requires_tools": {
                        "type": "boolean",
                        "description": "whether tools are needed for this query"
                    },
                    "direct_response": {
                        "type": "string",
                        "description": "response when no tools are needed",
                        "optional": True
                    },
                    "thought": {
                        "type": "string",
                        "description": "reasoning about how to solve the task (required in all cases)"
                    },
                    "plan": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "tool": {"type": "string"},
                                        "args": {"type": "object"}
                                    }
                                }
                            ]
                        }
                    },
                    "tool_calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {
                                    "type": "string",
                                    "description": "name of the tool"
                                },
                                "args": {
                                    "type": "object",
                                    "description": "parameters for the tool"
                                }
                            }
                        },
                        "description": "tools to call in sequence (when tools are needed)",
                        "optional": True
                    }
                },
                "examples": [
                    # 无工具调用示例
                    {
                        "query": "How are you doing?",
                        "response": {
                            "requires_tools": False,
                            "direct_response": "I'm an AI assistant here to help with your questions!",
                            "thought": "This is a friendly greeting. No tools are needed to respond."
                        }
                    },
                    {
                        "query": "你好，最近怎么样？",
                        "response": {
                            "requires_tools": False,
                            "direct_response": "我很好，谢谢关心！我是您的GIS助手，随时准备帮助您处理地理空间分析任务。",
                            "thought": "这是友好的问候，无需使用工具，直接回复即可"
                        }
                    },
                    {
                        "query": "你喜欢哪种编程语言？",
                        "response": {
                            "requires_tools": False,
                            "direct_response": "作为AI，我没有个人喜好，但我可以处理多种编程语言的任务。",
                            "thought": "这是一个关于个人喜好的问题，无需使用工具，直接回复即可"
                        }
                    },
                    # 需要工具调用示例
                    {
                        "query": "列出当前工程中的所有图层",
                        "response": {
                            "requires_tools": True,
                            "thought": "用户需要获取当前工程中的图层列表，使用list_project_layers工具即可满足需求",
                            "plan": [
                                "调用list_project_layers工具获取图层信息"
                            ],
                            "tool_calls": [
                                {
                                    "tool": "list_project_layers"
                                }
                            ]
                        }
                    },
                    {

                        "query": "为武汉地块创建一个500米的缓冲区",
                        "response": {
                            "requires_tools": True,
                            "thought": "用户需要创建一个500米的缓冲区，这需要使用create_buffer工具，并传入相应的参数，如输入图层和缓冲距离。工具将处理数据并生成所需的缓冲区。",
                            "plan": [
                                "调用create_buffer工具，输入图层为武汉地块，缓冲距离为500米"
                            ],
                            "tool_calls": [
                                {
                                    "tool": "create_buffer",
                                    "args": {
                                        "input_layer": "武汉地块",
                                        "distance": 500
                                    }
                                }
                            ]
                        }
                    },
                    {
                        "query": "Create a 500-meter buffer zone for the 武汉地块",
                        "response": {
                            "requires_tools": True,
                            "thought": "The user has asked to create a buffer zone of 500 meters. This requires using the 'create_buffer' tool with the appropriate parameters such as input_layer and distance. The tool will process the data and generate the desired buffer zone.",
                            "plan": [
                                "调用create_buffer工具，输入图层为武汉地块，缓冲距离为500米"
                            ],
                            "tool_calls": [
                                {
                                    "tool": "create_buffer",
                                    "args": {
                                        "input_layer": "武汉地块",
                                        "distance": 500
                                    }
                                }
                            ]
                        }
                    },
                    {
                        "query": "对道路图层和水域图层进行交集分析",
                        "response": {
                            "requires_tools": True,
                            "thought": "用户要求进行交集分析，需要使用 vector_overlay 工具",
                            "plan": [
                                "调用 vector_overlay 工具进行交集分析"
                            ],
                            "tool_calls": [
                                {
                                    "tool": "vector_overlay",
                                    "args": {
                                        "input_layer": "道路",
                                        "overlay_layer": "水域",
                                        "overlay_type": "intersection"
                                    }
                                }
                            ]
                        }
                    },
                    {
                        "query": "将DEM图层的分辨率重采样到30米",
                        "response": {
                            "requires_tools": True,
                            "thought": "用户需要将DEM图层重采样到30米分辨率，这需要使用resample_raster工具",
                            "plan": [
                                "调用resample_raster工具，设置目标分辨率为30米"
                            ],
                            "tool_calls": [
                                {
                                    "tool": "resample_raster",
                                    "args": {
                                        "input_raster": "DEM",
                                        "target_resolution": 30,
                                        "resampling_method": "bilinear"
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
        return f"""You are an AI assistant that helps users by providing direct answers or using tools when necessary.
Configuration, instructions, and available tools are provided in JSON format below:

{json.dumps(tools_json, indent=2)}

**Critical Format Rules**:
- You MUST return a **strictly valid JSON object** matching the response_format schema.
- The JSON must start with '{{' and end with '}}' with no other text outside.
- All strings must use double quotes. Do not use comments or markdown formatting.
- If no tools are needed, set "requires_tools": false and provide "direct_response".
- **You must always provide a 'thought' field**, even if no tools are used. The 'thought' should explain your reasoning.

**Behavior Rules**:
- If the user asks a greeting or simple question needing no tools (e.g., "How are you?"),
set "requires_tools": false, provide a friendly "direct_response", and explain your reasoning in "thought".
- Never leave "direct_response" empty when "requires_tools" is false.
"""

    def plan(self, user_query: str) -> Dict:
        """规划如何处理用户查询"""
        messages = [
            {"role": "system", "content": self.create_system_prompt()},
            {"role": "user", "content": user_query}
        ]

        response = self.client.chat.completions.create(
            # 使用本地部署的大模型
            # model=self.model,
            # 使用网络的API密钥
            model="deepseek-v3.1",

            messages=messages,
            temperature=0,
            # 如果Ollama支持，添加以下参数强制JSON响应
            # response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content

        # 日志记录AI原始响应内容和完整response对象
        self.logger.info(f"[AI返回内容] 用户Query: {user_query}")
        self.logger.info(f"[AI原始JSON] {content}")
        self.logger.info(f"[AI完整response对象] {response}")

        try:
            # 增强JSON解析：处理模型可能返回的非标准JSON
            if not content.strip().startswith("{"):
                # 尝试提取可能的JSON片段
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group()

            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[DEBUG] Invalid JSON content:\n{content}")
            # 返回包含错误信息的结构化响应
            return {
                "requires_tools": False,
                "direct_response": f"解析响应时出错: {str(e)}",
                "thought": "模型返回了无效的JSON格式"
            }

    def execute(self, user_query: str) -> str:
        """执行查询计划并返回结果"""
        try:
            plan = self.plan(user_query)
            requires_tools = plan.get("requires_tools", True)
            direct_response = plan.get("direct_response", "")
            thought = plan.get('thought', '')

            # 调试日志
            print(f"[DEBUG] Plan received: {json.dumps(plan, indent=2)}")

            # 日志记录AI决策内容
            self.logger.info(f"[AI决策] 用户Query: {user_query}")
            self.logger.info(f"[AI计划JSON] {json.dumps(plan, ensure_ascii=False, indent=2)}")
            self.logger.info(f"[AI思考] {thought}")

            plan_steps = []
            raw_plan = plan.get('plan', [])
            if isinstance(raw_plan, list):
                for step in raw_plan:
                    if isinstance(step, dict):  # 处理字典格式的步骤
                        if 'tool' in step:  # 工具调用步骤
                            plan_steps.append(f"调用工具 {step.get('tool', '')} 参数: {step.get('args', {})}")
                        else:  # 普通描述步骤
                            plan_steps.append(json.dumps(step))
                    else:  # 普通字符串步骤
                        plan_steps.append(str(step))
                plan_steps = '. '.join(plan_steps)
            else:
                plan_steps = str(raw_plan)

            results = []
            missing_tools = []  # 记录缺失的工具
            tool_calls = []  # 初始化 tool_calls 为空列表

            # 统一构造响应结构
            if not requires_tools:
                if not direct_response:
                    # 尝试从thought中提取直接响应
                    if "直接回答" in thought or "无需工具" in thought:
                        match = re.search(r"直接回答[:：]\s*(.+)", thought)
                        if match:
                            direct_response = match.group(1)
                        else:
                            direct_response = thought.split("。")[0]
                    else:
                        results.append("错误：当不需要工具时需要直接响应内容")
                    results.append(direct_response if direct_response else "无直接响应内容")
                else:
                    results.append(direct_response)
            else:
                # 优先使用显式的 tool_calls
                if "tool_calls" in plan and isinstance(plan["tool_calls"], list):
                    tool_calls = plan["tool_calls"]
                # 如果 tool_calls 为空，尝试从 plan 字段解析
                elif not tool_calls and "plan" in plan:
                    if isinstance(plan["plan"], list):
                        tool_calls = [
                            step for step in plan["plan"]
                            if isinstance(step, dict) and "tool" in step
                        ]

                if not tool_calls:
                    results.append("错误: 需要工具但未生成工具调用")
                    thought += " 警告：计划需要工具但未生成有效的工具调用指令。"
                else:
                    for tool_call in tool_calls:
                        tool_name = tool_call.get("tool", "")
                        tool_args = tool_call.get("args", {})
                        if not tool_name:
                            results.append("错误: 工具调用中缺少工具名称")
                            continue
                        if tool_name not in self.tools:
                            missing_tools.append(tool_name)  # 记录缺失的工具名
                            results.append(f"错误: 工具  '{tool_name}' 不存在")
                            continue
                        try:
                            result = self.use_tool(tool_name, **tool_args)
                            results.append(result)
                        except Exception as e:
                            results.append(f"使用工具  {tool_name} 错误: {str(e)}")

            # 处理缺失工具的情况，更新思考内容
            final_thought = thought.strip() or "AI未提供详细推理过程."
            if missing_tools:
                final_thought += f" 但是所需工具 '{', '.join(missing_tools)}' 不可用."

            # 处理无工具调用但需要工具的情况
            if requires_tools and (not tool_calls or missing_tools):
                final_thought += " 当前工具集无法完全处理此查询。"
            final_plan = plan_steps if plan_steps.strip() else "直接响应不需要具体计划。"
            # 强制包含所有字段
            return f"""🔍: {user_query}
    💭: {final_thought}
    📋: {final_plan}
    ✅: {' | '.join(results) if results else '无结果生成'}"""

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[ERROR] 执行失败: {tb}")
            return f"""🔍: {user_query}
    💭: 系统在处理过程中遇到错误
    📋: 错误处理
    ✅: 系统错误: {str(e)}"""

    def execute_async(self, user_query: str, callback: Callable[[str], None]):
        """异步执行查询"""

        def run():
            try:
                result = self.execute(user_query)
                callback(result)
            except Exception as e:
                callback(f"系统错误: {str(e)}")

        thread = threading.Thread(target=run)
        thread.start()

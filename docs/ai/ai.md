# AI 模块

> 本文档介绍 `baibao.ai` 模块的使用方法，包含 LLM 大语言模型和 OCR 文字识别两大子模块。

## 模块概述

`baibao.ai` 模块提供统一的 AI 能力接口，采用策略模式设计，支持运行时自由切换后端实现：

| 子模块 | 功能 | 说明 |
|--------|------|------|
| `baibao.ai.llm` | 大语言模型 | 支持单轮/多轮对话、流式输出，兼容 OpenAI API 格式 |
| `baibao.ai.ocr` | 文字识别 | 策略 + 模板方法设计，内置 EasyOCR / PaddleOCR，支持自定义引擎扩展 |

---

## LLM 子模块

### 基本使用

```python
from baibao.ai.llm import chat, set_llm_service, LlmMessage
from baibao.ai.llm.openai_llm import OpenAiLlm

# 1. 设置 LLM 服务
set_llm_service("default", OpenAiLlm(
    api_key="sk-xxx",
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
))

# 2. 单轮对话
response = chat([LlmMessage(role="user", content="你好，请介绍一下自己")])
print(response.content)
```

### 支持的服务商

`OpenAiLlm` 兼容所有 OpenAI API 格式的服务商：

```python
# OpenAI 官方
llm = OpenAiLlm(api_key="sk-xxx")

# DeepSeek
llm = OpenAiLlm(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
)

# 月之暗面 (Moonshot)
llm = OpenAiLlm(
    api_key="sk-xxx",
    base_url="https://api.moonshot.cn/v1",
    model="moonshot-v1-8k",
)

# 智谱 AI (GLM)
llm = OpenAiLlm(
    api_key="xxx",
    base_url="https://open.bigmodel.cn/api/paas/v4",
    model="glm-4",
)

# 本地 Ollama
llm = OpenAiLlm(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
    model="llama3",
)
```

### 环境变量配置

支持通过环境变量自动获取配置，无需硬编码：

```bash
# 设置环境变量
export OPENAI_API_KEY="sk-xxx"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 或使用 DeepSeek 的环境变量
export DEEPSEEK_API_KEY="sk-xxx"
```

```python
# 自动从环境变量读取
llm = OpenAiLlm()  # 使用默认模型 gpt-4o-mini
```

### 多配置管理

可以设置多个 LLM 配置，按需切换：

```python
from baibao.ai.llm import chat, set_llm_service, LlmMessage
from baibao.ai.llm.openai_llm import OpenAiLlm

# 设置多个配置
set_llm_service("fast", OpenAiLlm(model="gpt-4o-mini"))
set_llm_service("code", OpenAiLlm(model="gpt-4o"))

# 使用指定配置
response = chat([LlmMessage(role="user", content="写一个快排算法")], llm_name="code")
response = chat([LlmMessage(role="user", content="简要概括")], llm_name="fast")
```

### 多轮对话

```python
from baibao.ai.llm import chat, LlmMessage

messages = [
    LlmMessage(role="system", content="你是一个有用的助手"),
    LlmMessage(role="user", content="你好"),
    LlmMessage(role="assistant", content="你好！有什么可以帮助你的吗？"),
    LlmMessage(role="user", content="今天天气怎么样？"),
]

response = chat(messages)
print(response.content)
```

### 流式输出

```python
from baibao.ai.llm import stream_chat, LlmMessage

# 单轮流式输出
for chunk in stream_chat([LlmMessage(role="user", content="讲一个关于人工智能的故事")]):
    print(chunk, end="", flush=True)

# 多轮流式输出
messages = [
    LlmMessage(role="user", content="你好"),
    LlmMessage(role="assistant", content="你好！有什么可以帮你的？"),
    LlmMessage(role="user", content="讲个笑话"),
]
for chunk in stream_chat(messages):
    print(chunk, end="", flush=True)
```

### 响应对象

`LlmResponse` 包含丰富的元数据：

```python
from baibao.ai.llm import chat, LlmMessage

response = chat([LlmMessage(role="user", content="你好")])

print(response.content)        # 响应文本
print(response.model)          # 使用的模型名称
print(response.usage)          # token 使用情况
# {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
print(response.finish_reason)  # 结束原因: 'stop'、'length' 等
```

### API 参数

对话方法支持多种参数控制生成行为：

```python
response = chat(
    messages=[
        LlmMessage(role="system", content="你是一个诗人"),
        LlmMessage(role="user", content="写一首诗"),
    ],
    temperature=0.9,    # 温度，越高越随机 (0~2)
    max_tokens=500,     # 最大生成 token 数
    top_p=0.9,          # 核采样参数
    frequency_penalty=0.5,  # 频率惩罚
)
```

### 管理函数

```python
from baibao.ai.llm import get_llm_service, set_llm_service, remove_llm_service

# 设置服务
set_llm_service("my_llm", OpenAiLlm(api_key="sk-xxx"))

# 获取服务
llm = get_llm_service("my_llm")

# 移除服务
remove_llm_service("my_llm")
```

---

## OCR 子模块

### 基本使用

```python
from baibao.ai.ocr import recognize

# 识别图片文字（默认使用 EasyOCR）
text = recognize("invoice.png")
print(text)
```

### 支持的 OCR 引擎

| 引擎 | 类名 | 特点 |
|------|------|------|
| EasyOCR | `EasyOcr` | 基于 PyTorch，支持 80+ 语言，支持 GPU 加速（baibao 命令默认） |
| PaddleOCR（自动分发） | `PaddleOcr` | 按已装 paddleocr 版本自动委托 `PaddleOcrV2`/`PaddleOcrV3`，中文精度高 |
| PaddleOCR 2.x | `PaddleOcrV2` | 显式 2.x API（`use_angle_cls` + `.ocr()`） |
| PaddleOCR 3.x | `PaddleOcrV3` | 显式 3.x API（`use_textline_orientation` + `.predict()`） |

### 切换 OCR 引擎

```python
from baibao.ai.ocr import recognize, set_ocr_engine
from baibao.ai.ocr.paddle_ocr import PaddleOcr
from pykunlun.ai.ocr import OcrCfg

# 切换为 PaddleOCR
set_ocr_engine("paddle", PaddleOcr(OcrCfg(lang='ch')))

# 使用指定引擎
text = recognize("image.png", ocr_name="paddle")
```

### 统一配置构造（OcrCfg + build_ocr_engine）

各引擎构造参数不同（EasyOCR 的 `langs`/`gpu`、PaddleOCR 的 `lang`/`device`/`cpu_threads` 等），
直接构造需记住每个引擎的差异。`OcrCfg` + `build_ocr_engine` 提供引擎无关的统一入口，
参数映射在各实现类内部完成：

```python
from baibao.ai.ocr import OcrCfg, build_ocr_engine

# 统一配置：语言 / GPU / CPU 线程 / 角度分类
cfg = OcrCfg(lang='en', gpu=False, cpu_threads=8, use_angle_cls=True)

# 按引擎名 + 配置构造（引擎差异由各实现类吸收）：easy / paddle / paddle2 / paddle3
ocr = build_ocr_engine('paddle', cfg)
text = ocr.recognize("image.png")
```

`OcrCfg` 字段（只收敛各引擎通用的；``server`` 的连接参数不在此，见下）：

| 字段 | 说明 |
|------|------|
| `lang` | 语言码：`ch`（中英，默认）、`en`、`japan`、`ko`、`ch_tra` |
| `gpu` | 启用 GPU（easyocr→`gpu`；paddle→`device='gpu:0'`；需 GPU 版依赖） |
| `cpu_threads` | CPU 推理线程数（仅 paddle 生效） |
| `use_angle_cls` | 方向/角度分类（paddle 2.x→`use_angle_cls`，3.x→`use_textline_orientation`；easyocr 忽略） |

> `OcrCfg` 与抽象基类 `OcrEngine`、管理器 `OcrManager` 收敛在 `pykunlun.ai.ocr`，
> baibao 在此之上提供 EasyOCR / PaddleOCR 的具体实现（带重依赖）并再导出。
> 不想用统一配置时，也可直接构造各引擎类（`EasyOcr(cfg)` / `PaddleOcr(cfg)` 等），
> 或用 `set_ocr_engine` 注册具名实例后 `recognize(path, ocr_name=...)`。

> ``server`` 引擎的专属参数（服务端地址 `server_url`、要求服务端使用的引擎 `server_engine`、
> 超时 `timeout`）**不放入通用 `OcrCfg`**，而由 `ServerOcr` 自身构造参数表达：
> `ServerOcr(cfg, server_url=..., server_engine=..., timeout=...)`；走工厂时用
> `build_ocr_engine('server', cfg, server_url=..., server_engine=..., server_timeout=...)`。
> `server_url` 缺省时取环境变量 `BAIBAO_OCR_SERVER_URL`，再退回默认 `http://127.0.0.1:8000`。

### EasyOCR 配置

```python
from baibao.ai.ocr.easy_ocr import EasyOcr
from pykunlun.ai.ocr import OcrCfg

# 默认配置（中文 + 英文）
ocr = EasyOcr(OcrCfg())

# 多语言配置（lang 码由 EasyOcr 内部映射为语言列表，如 'japan' -> ['ja','en']）
ocr = EasyOcr(OcrCfg(lang='japan'))

# 启用 GPU 加速
ocr = EasyOcr(OcrCfg(gpu=True))
```

### PaddleOCR 配置

`PaddleOcr` 是**自动分发器**：实例化时按本地已装的 paddleocr 主版本号，自动委托 `PaddleOcrV2`（2.x）或 `PaddleOcrV3`（3.x）。构造参数对两版都兼容——`use_angle_cls` 在 V3 内部映射为 `use_textline_orientation`。

```python
from baibao.ai.ocr.paddle_ocr import PaddleOcr
from pykunlun.ai.ocr import OcrCfg

ocr = PaddleOcr(OcrCfg())                      # 自动选 V2/V3，默认中英文
ocr = PaddleOcr(OcrCfg(use_angle_cls=False))   # 关角度/行方向分类（更快）
ocr = PaddleOcr(OcrCfg(lang='en'))             # 英文
print(ocr.impl)                                # 'paddle3' 或 'paddle2'，便于排查
```

也可直接指定版本（需对应 paddleocr 已装）：

```python
from baibao.ai.ocr.paddle_ocr import PaddleOcrV2, PaddleOcrV3
ocr = PaddleOcrV3(OcrCfg())   # 强制 3.x
ocr = PaddleOcrV2(OcrCfg())   # 强制 2.x
```

### PaddleOCR 版本与已知坑（重要）

PaddleOCR 依赖 paddlepaddle（CPU 版 `paddlepaddle`），在新版 Python（3.13）/ Windows 上踩过的坑：

| 现象 | 原因 | 处理 |
|------|------|------|
| `import torch` 报 `c10.dll 初始化失败`（WinError 1114） | 缺微软 VC++ 运行库，或 torch wheel 与 Python 不匹配 | 装 **Visual C++ Redistributable 2015-2022 (x64)**；或 `pip install --force-reinstall torch` |
| 推理报 `ConvertPirateAttribute2RuntimeAttribute not support ...` | paddlepaddle 3.3.x 在 CPU + mkldnn 下的 PIR bug | **baibao 已内置绕过**：`PaddleOcr` / `PaddleOcrV3` 构造时传 `enable_mkldnn=False`（关 mkldnn 走纯 CPU），调用方无感；CPU 慢可用 `--cpu-threads N` 或 `OcrCfg(cpu_threads=N)` 提速 |
| 模型加载阶段报 `strides` 属性类型不对 | paddlepaddle **3.0.0** 与 PP-OCRv6 不兼容 | paddlepaddle **用 3.3.x，别用 3.0.0** |

> 关闭 mkldnn 后 CPU 推理会比开 mkldnn 慢一些，但功能正常。注意 easy 引擎也依赖 torch——torch 修不好，easy 同样跑不了，届时只能换 Python 3.11/3.12 环境。

**CPU 环境下的良性告警**：paddleocr / paddlex / torch 在无 GPU 时会打印若干 `UserWarning`，属第三方库内部行为，**不影响识别结果**，baibao 不做拦截（也没有可改的开关）：

| 告警 | 含义 | 是否需处理 |
|------|------|-----------|
| `'pin_memory' argument is set as true but no accelerator is found` | torch 的 DataLoader 默认开 `pin_memory`（本意加速 CPU→GPU 拷贝），无 GPU 时自动退化为普通内存 | 否，纯 CPU 跑结果完全正常 |
| `No ccache found. Please be aware that recompilation ...` | paddle 未找到 C/C++ 编译缓存工具 ccache，仅影响即时编译（JIT）源码的编译耗时，**纯推理不触发** | 否 |
| `Creating model: (...)` / 模型下载进度条 | paddlex 加载子模型（检测 / 识别 / 方向分类）的正常日志 | 否，属正常输出 |

> 若一定要消掉 `pin_memory` 告警，可构造引擎后自行设 `import warnings; warnings.filterwarnings('ignore', message='.*pin_memory.*')`，但更推荐直接忽略。

### 详细识别结果

获取文字位置和置信度：

```python
from baibao.ai.ocr import recognize_with_details

results = recognize_with_details("image.png")

for item in results:
    print(f"文本: {item.text}")
    print(f"位置: {item.bbox}")      # [(x1,y1), (x2,y2), (x3,y3), (x4,y4)]
    print(f"置信度: {item.confidence:.2f}")
    print("---")
```

### 可视化绘制

在图片上绘制识别结果：

```python
from baibao.ai.ocr import recognize_and_draw

# 识别并绘制边界框，保存结果图
img = recognize_and_draw(
    "image.png",
    output_path="result.png",     # 保存路径
    color=(0, 255, 0),            # 绿色边界框
    thickness=2,                  # 线条粗细
)
```

### 使用 OpenCV 图像数组

支持直接传入 OpenCV 图像数组：

```python
import cv2
from baibao.ai.ocr import recognize

# 读取图片
img = cv2.imread("image.png")

# 直接传入图像数组
text = recognize(img)
```

传入的图像数组会被内部复制，**不会修改原图**；识别接口同时也支持直接传入图片路径（字符串）。

### OCR 管理函数

```python
from baibao.ai.ocr import get_ocr_engine, set_ocr_engine, remove_ocr_engine
from baibao.ai.ocr.paddle_ocr import PaddleOcr
from pykunlun.ai.ocr import OcrCfg

# 设置引擎
set_ocr_engine("my_ocr", PaddleOcr(OcrCfg()))

# 获取引擎
ocr = get_ocr_engine("my_ocr")

# 移除引擎
remove_ocr_engine("my_ocr")
```

管理函数背后由一个 `OcrManager` 单例（来自 `pykunlun.ai.ocr`）托管，内部加锁保护，是**线程安全**的，可在多线程环境下并发调用。默认配置（不传 `ocr_name`）在首次访问时会自动创建 `EasyOcr` 实例。

> 需要多管理器实例、或按引擎类型工厂化创建时，可直接使用 `OcrManager`：
> `register_engine_class(EasyOcr)` 注册实现类后，`register("default", OcrCfg(engine_name='easy'))`
> 即可按 `cfg.engine_name` 自动 new 实例。详见 `pykunlun.ai.ocr.OcrManager`。

### 自定义 OCR 引擎

`OcrEngine` 采用模板方法设计：扩展自定义引擎只需继承并实现核心方法 `_recognize_array`，图片加载、文本清洗与结果绘制均由基类统一负责，对外行为与内置引擎完全一致。

```python
from baibao.ai.ocr import OcrEngine, OcrResult, set_ocr_engine, recognize
from pykunlun.ai.ocr import OcrCfg

class MyOcr(OcrEngine):
    engine_name = 'my'  # 类级常量：标识本引擎类型

    def __init__(self, cfg: OcrCfg):
        super().__init__(cfg)  # 绑定并校验配置（构造即校验）

    def _recognize_array(self, image):
        # image 是已校验的 OpenCV 图像数组（BGR），无需自行读取文件或校验路径
        raw = my_engine.detect(image)
        return [
            OcrResult(text=t, bbox=b, confidence=c)
            for b, t, c in raw
        ]

# 注册后即可像内置引擎一样使用
set_ocr_engine("mine", MyOcr(OcrCfg()))
text = recognize("image.png", ocr_name="mine")
```

---

## 依赖说明

### LLM 子模块

| 依赖包 | 说明 | 安装方式 |
|--------|------|----------|
| openai | OpenAI 官方 SDK | 首次使用自动安装 |

### OCR 子模块

| 依赖包 | 说明 | 安装方式 |
|--------|------|----------|
| easyocr | EasyOCR 引擎 | 首次使用自动安装 |
| opencv-python | 图像处理 | easyocr/paddleocr 依赖 |
| paddleocr | PaddleOCR 引擎 | 首次使用自动安装 |

---

## 完整示例

### LLM 多服务商切换

```python
from baibao.ai.llm import chat, set_llm_service, LlmMessage
from baibao.ai.llm.openai_llm import OpenAiLlm

# 配置多个服务商
set_llm_service("gpt", OpenAiLlm(
    api_key="sk-xxx",
    model="gpt-4o",
))

set_llm_service("deepseek", OpenAiLlm(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
))

# 按需切换
response1 = chat([LlmMessage(role="user", content="你好")], llm_name="gpt")
response2 = chat([LlmMessage(role="user", content="你好")], llm_name="deepseek")
```

### OCR 批量识别

```python
import os
from baibao.ai.ocr import recognize

# 批量识别目录下的图片
image_dir = "./images"
for filename in os.listdir(image_dir):
    if filename.endswith(('.png', '.jpg', '.jpeg')):
        path = os.path.join(image_dir, filename)
        text = recognize(path)
        print(f"{filename}: {text}")
```

### OCR 结果筛选高置信度

```python
from baibao.ai.ocr import recognize_with_details

results = recognize_with_details("image.png")

# 只保留置信度大于 0.8 的结果
high_confidence = [r for r in results if r.confidence > 0.8]

for item in high_confidence:
    print(f"{item.text} ({item.confidence:.1%})")
```

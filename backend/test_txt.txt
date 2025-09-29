# 教育AI问答系统

一个支持多学科问答的AI服务，特别针对数学问题提供可视化图形生成功能。

## 功能特性

- 🎓 **多学科支持**: 数学、语文、英语
- 📊 **数学可视化**: 自动生成函数图像、几何图形等
- 🌊 **流式输出**: 支持SSE实时响应
- 💾 **智能缓存**: Redis缓存提升响应速度
- 🐳 **容器化部署**: Docker Compose一键部署
- 🔒 **安全执行**: 沙箱环境执行代码生成
- 🚀 **清华源加速**: 使用清华大学PyPI镜像源，下载更快

## 系统架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Answer        │    │ Visualization   │    │     Redis       │
│   Service       │    │    Service      │    │   (缓存层)      │
│   (Port 8000)   │◄──►│   (Internal)    │    │   (Port 6379)   │
│ + Static Files  │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

**统一端口访问：**
- 🌐 **API接口**: `http://localhost:8000/v1/`
- 🖼️ **可视化图片**: `http://localhost:8000/static/`
- 📚 **API文档**: `http://localhost:8000/docs`

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd shenxue-ai

# 复制环境变量文件
cp env.example .env

# （可选）配置本地pip使用清华源
cp pip.conf ~/.pip/pip.conf  # Linux/Mac
# 或者 copy pip.conf %APPDATA%\pip\pip.ini  # Windows
```

### 2. 配置环境变量

编辑 `.env` 文件，设置你的阿里云百炼API密钥：

```bash
DASHSCOPE_API_KEY=sk-your_api_key_here
```

### 3. 启动服务

```bash
直接使用docker-compose
docker compose up -d --build
```


## API 使用

### 主要接口

#### 1. 问答接口（流式）

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "math",
    "text": "画出函数 y = x² 的图像",
    "stream": true
  }'
```


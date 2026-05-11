# ScholarMind 前端

> 项目入口与功能说明见根目录 [`README.md`](../README.md)。本文件**只承载前端独有的开发命令与目录约定**。

## 启动

前置：Node.js 18+、`npm`/`pnpm`，且后端 API 已运行在 `http://localhost:8000`（见 [`../backend/README.md`](../backend/README.md)）。

```bash
npm install
npm run dev          # 启动开发服务器，默认 http://localhost:5173
npm run build        # 生产构建，产物在 dist/
npm run lint         # ESLint
```

## 目录结构

```text
src/
├── api/           # API client 与请求封装
├── components/    # 可复用 UI 组件
├── pages/         # 页面级组件
├── router/        # 路由配置
├── store/         # 状态管理（Valtio）
├── utils/         # 工具函数
└── assets/        # 静态资源
```

## 环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `VITE_API_BASE` | 后端 API 入口；开发时走 Vite 代理，生产必须为绝对地址 | `/api` |
| `VITE_TITLE` | 页面标题 | `ScholarMind` |
| `VITE_ENABLE_ADMIN_UI` | 管理后台 UI 入口开关 | `true` |
| `VITE_DEMO_ENTRY_ENABLED` | `/demo` 免登录入口开关 | — |

完整变量及生产覆盖见 `frontend/.env.example`。

## 进一步阅读

- [项目入口（根 README）](../README.md) — 功能、技术栈、架构图
- [`../backend/README.md`](../backend/README.md) — 后端启动 / Tunnel / DB 迁移

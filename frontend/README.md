# ScholarMind - Frontend

> React + TypeScript + Vite powered frontend for ScholarMind.

## 🚀 Quick Start

### Prerequisites

- Node.js 18+ and npm/pnpm
- Backend API running at `http://localhost:8000` (see `../backend/README.md`)

### Development

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173`.

## 🛠️ Tech Stack

- **Framework**: React 18 with TypeScript
- **Build Tool**: Vite
- **UI Library**: Ant Design
- **State Management**: Valtio
- **Routing**: React Router v6
- **HTTP Client**: Axios
- **Code Style**: ESLint + Prettier

## 📦 Build for Production

```bash
npm run build
```

The production-ready bundle will be in the `dist/` directory.

## 🔧 Project Structure

```
src/
├── api/           # API client and request utilities
├── components/    # Reusable UI components
├── pages/         # Page-level components
├── router/        # Routing configuration
├── store/         # State management (Valtio)
├── utils/         # Helper functions
└── assets/        # Static resources
```

## 🎨 Features

- **Session-based Chat**: Multi-turn conversation with streaming responses
- **Document Management**: Upload, parse, and manage academic papers
- **Knowledge Base**: Organize documents into collections
- **Citation Visualization**: Track and navigate paper citations
- **Cross-Paper Comparison**: Side-by-side analysis (backend feature integration)
